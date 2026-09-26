import json
import re
import os
import sqlite3
import uuid
import pandas as pd
from flask import Flask, request, jsonify, render_template, send_from_directory, session
from werkzeug.utils import secure_filename
from google import genai

# ---------------------------------------------------------------------------
# Reuses the same logic as insightai.py (load_csv_to_sqlite, ask_gemini_for_sql,
# run_query, maybe_generate_chart) but wrapped behind Flask routes instead of
# a CLI loop.
#
# Multi-user isolation: each visitor gets their own session (a signed cookie
# holding a random session ID). That ID is used to build a per-session table
# name and per-session chart filenames, so two people using the live app at
# the same time never see or overwrite each other's data.
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Needed for Flask's session cookies to work. In production (Render), set
# SECRET_KEY as an environment variable so sessions survive server restarts.
# Locally, falls back to a random key generated at startup (fine for testing;
# it just means everyone's session resets if you restart Flask).
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

UPLOAD_DIR = "uploads"
CHART_DIR = "charts"
DB_PATH = "data.db"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

# Per-session state, keyed by session ID (not shared globally between users).
# Each entry: {"schema": ..., "table_name": ...}
SESSIONS = {}


def get_session_id():
    """Returns this visitor's session ID, creating one if it doesn't exist yet."""
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def load_csv_to_sqlite(csv_path, table_name, db_path=DB_PATH):
    df = pd.read_csv(csv_path)
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()

    schema_lines = []
    for col, dtype in df.dtypes.items():
        schema_lines.append(f"{col} ({dtype})")
    schema_str = f"Table: {table_name}\nColumns: " + ", ".join(schema_lines)

    preview_rows = df.head(5).fillna("").astype(str).values.tolist()
    row_count = len(df)

    return schema_str, list(df.columns), preview_rows, row_count, df


def ask_gemini_for_sql(question, schema, table_name, retry_context=None):
    client = get_gemini_client()
    if client is None:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Run: $env:GEMINI_API_KEY='your-key-here' "
            "then restart the Flask server."
        )

    retry_note = ""
    if retry_context:
        retry_note = f"""

Note: a previous attempt to answer this question produced a query that
failed with this issue: "{retry_context}"
Please write a corrected query that avoids this problem.
"""

    prompt = f"""You are a SQL generator. Given this table schema:

{schema}

Write a single SQLite SELECT query that answers this question:

"{question}"
{retry_note}
Rules:
- Return ONLY the raw SQL query, no markdown, no explanation, no code fences.
- Only use SELECT statements. Never modify data.
- Use the exact table name: {table_name}
"""

    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
    )
    sql = response.text.strip()
    sql = sql.replace("```sql", "").replace("```", "").strip()
    return sql


# ---------------------------------------------------------------------------
# SQL safety layer
#
# The prompt above ASKS Gemini to only produce SELECT statements, but a
# prompt is just a request -- it does not guarantee anything. A cleverly
# worded question could trick the model into returning something like
# "DELETE FROM data_table" or "DROP TABLE data_table". We never trust model
# output blindly, so every generated query is validated here in code before
# it's allowed anywhere near the database. This is defense at the
# application layer, independent of whatever the model actually did.
# ---------------------------------------------------------------------------

# Any query containing these keywords (as whole SQL statements/clauses) is
# rejected outright, regardless of what the prompt asked for.
FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "create",
    "truncate", "replace", "attach", "detach", "pragma",
    "vacuum", "reindex", "grant", "revoke",
]


class UnsafeSQLError(Exception):
    """Raised when a generated query fails the SELECT-only safety check."""
    pass


def validate_sql_is_safe(sql, allowed_table_name):
    """
    Enforces that the SQL is a single, read-only SELECT statement that only
    touches the caller's own table. Raises UnsafeSQLError if the query looks
    like it could modify data, contains multiple statements, references a
    different table (e.g. another session's data), or isn't a SELECT at all.
    """
    cleaned = sql.strip().rstrip(";").strip()

    if not cleaned:
        raise UnsafeSQLError("Generated query was empty.")

    # Reject multiple statements chained with semicolons (e.g.
    # "SELECT * FROM data_table; DROP TABLE data_table")
    if ";" in cleaned:
        raise UnsafeSQLError("Multiple SQL statements are not allowed.")

    lowered = cleaned.lower()

    # Must start with SELECT -- no exceptions.
    if not lowered.startswith("select"):
        raise UnsafeSQLError("Only SELECT queries are allowed.")

    # Even inside a SELECT, reject if any forbidden keyword shows up as a
    # standalone word (e.g. blocks a subquery or comment trick that sneaks
    # in "drop table ..." disguised inside the query text).
    
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            raise UnsafeSQLError(f"Query contains a disallowed keyword: {keyword.upper()}")

    # Isolation check: the query must reference this session's own table.
    # This stops one visitor's query from ever being able to read another
    # visitor's uploaded data, even by accident or a crafted question.
    if allowed_table_name.lower() not in lowered:
        raise UnsafeSQLError("Query does not reference the expected table for this session.")

    return cleaned


def run_query(sql, allowed_table_name, db_path=DB_PATH):
    # Validate first -- reject anything that isn't a clean, single SELECT
    # that stays scoped to this session's own table.
    safe_sql = validate_sql_is_safe(sql, allowed_table_name)

    # Second, independent layer of defense: open SQLite itself in read-only
    # mode (mode=ro). Even if a malicious query somehow slipped past
    # validate_sql_is_safe, SQLite will refuse any write at the database
    # engine level and raise an error instead of touching data.db.
    db_uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    try:
        result_df = pd.read_sql_query(safe_sql, conn)
    finally:
        conn.close()
    return result_df


def maybe_generate_chart(result_df, filename_hint="chart"):
    if len(result_df) < 2:
        return None

    numeric_cols = result_df.select_dtypes(include="number").columns.tolist()
    non_numeric_cols = [c for c in result_df.columns if c not in numeric_cols]

    if not numeric_cols or not non_numeric_cols:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    label_col = non_numeric_cols[0]
    value_col = numeric_cols[0]

    plt.figure(figsize=(8, 5))
    plt.bar(result_df[label_col].astype(str), result_df[value_col])
    plt.xlabel(label_col)
    plt.ylabel(value_col)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()

    safe_name = secure_filename(filename_hint) or "chart"
    filename = f"{safe_name}.png"
    path = os.path.join(CHART_DIR, filename)
    plt.savefig(path)
    plt.close()
    return filename
def generate_insight(question, result_df):
    """
    Sends the query result back to Gemini with a small follow-up prompt,
    asking for one sentence of business interpretation.
    Returns None (never raises) if there's no key, no data, or the call
    fails -- an insight is a bonus, not something that should ever break
    the main /ask response.
    """
    client = get_gemini_client()
    if client is None or result_df.empty:
        return None

    result_text = result_df.head(20).to_string(index=False)

    prompt = f"""You are a business data analyst. A user asked this question about their data:

"{question}"

Here is the query result:
{result_text}

Write ONE short, plain-English sentence that interprets this result from a
business perspective. Focus on the most notable finding (a leader, a gap,
a concentration, a decline). Do not just restate the numbers -- give the
takeaway. No preamble, just the single sentence.
"""

    try:
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
        )
        return response.text.strip()
    except Exception:
        return None

def generate_sql_explanation(sql):
    """
    Sends the generated SQL to Gemini and asks for one plain-English
    sentence explaining what it does. Returns None (never raises) if
    there's no API key or the call fails -- this is a bonus explanation,
    not something that should ever block showing the actual result.
    """
    client = get_gemini_client()
    if client is None:
        return None

    prompt = f"""Explain what this SQL query does, in ONE short, plain-English
sentence a non-technical person would understand. No preamble, just the
sentence.

SQL:
{sql}
"""

    try:
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
        )
        return response.text.strip()
    except Exception:
        return None

def parse_suggested_questions(raw_text):
    """
    Gemini is asked to return a JSON array of question strings, but LLMs
    don't always follow formatting instructions perfectly. This tries
    clean JSON parsing first, then falls back to extracting quoted
    strings or numbered/bulleted lines, so a slightly-off response
    doesn't break the feature entirely.
    """
    cleaned = raw_text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    try:
        questions = json.loads(cleaned)
        if isinstance(questions, list):
            return [str(q).strip() for q in questions if str(q).strip()][:4]
    except (json.JSONDecodeError, ValueError):
        pass

    lines = re.findall(r'"([^"]+)"', cleaned)
    if lines:
        return lines[:4]

    lines = [
        re.sub(r'^[\d\.\-\*\)]+\s*', '', line).strip()
        for line in cleaned.split("\n") if line.strip()
    ]
    return [l for l in lines if l][:4]


def generate_suggested_questions(schema):
    """
    Given a dataset's schema, asks Gemini for 3-4 good starter questions
    a user could ask about it. Returns an empty list (never raises) if
    there's no API key or the call fails -- suggestions are a nice-to-have,
    not something that should ever block a successful upload.
    """
    client = get_gemini_client()
    if client is None:
        return []

    prompt = f"""Given this dataset schema:

{schema}

Suggest 3-4 good, specific analytical questions a user could ask about
this data. Return ONLY a JSON array of strings, no markdown, no
explanation. Example format: ["question 1", "question 2", "question 3"]
"""

    try:
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
        )
        return parse_suggested_questions(response.text)
    except Exception:
        return []

def generate_eda_summary(df):
    """
    For each column, computes a small summary using plain pandas -- no AI
    call needed. Numeric columns get mean/median/min/max, categorical
    columns get their top 3 most frequent values with counts.
    """
    summary = []

    for col in df.columns:
        series = df[col]
        missing_count = int(series.isna().sum())

        if pd.api.types.is_numeric_dtype(series):
            clean = series.dropna()
            if len(clean) == 0:
                continue
            summary.append({
                "column": col,
                "type": "numeric",
                "mean": round(float(clean.mean()), 2),
                "median": round(float(clean.median()), 2),
                "min": round(float(clean.min()), 2),
                "max": round(float(clean.max()), 2),
                "missing": missing_count,
            })
        else:
            clean = series.dropna()
            if len(clean) == 0:
                continue
            top_values = clean.value_counts().head(3)
            summary.append({
                "column": col,
                "type": "categorical",
                "top_values": [
                    {"value": str(val), "count": int(count)}
                    for val, count in top_values.items()
                ],
                "missing": missing_count,
            })

    return summary
# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    sid = get_session_id()
    table_name = f"data_{sid}"

    # Namespace the saved upload by session ID too, so two people uploading
    # a file with the same name don't overwrite each other on disk.
    filename = secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_DIR, f"{sid}_{filename}")
    file.save(save_path)

    try:
        schema, columns, preview_rows, row_count, df = load_csv_to_sqlite(save_path, table_name)
    except Exception as e:
        return jsonify({"error": f"Failed to load CSV: {e}"}), 400

    SESSIONS[sid] = {"schema": schema, "table_name": table_name}
    suggested_questions = generate_suggested_questions(schema)
    eda_summary = generate_eda_summary(df)

    return jsonify({
        "message": "File uploaded and loaded.",
        "schema": schema,
        "columns": columns,
        "preview_rows": preview_rows,
        "row_count": row_count,
        "filename": filename,
        "suggested_questions": suggested_questions,
        "eda_summary": eda_summary,
    })


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    sid = get_session_id()
    state = SESSIONS.get(sid)
    if not state or not state.get("schema"):
        return jsonify({"error": "No CSV uploaded yet. Upload a file first."}), 400

    table_name = state["table_name"]

    sql = None
    result_df = None
    last_error = None

    for attempt in range(2):
        try:
            sql = ask_gemini_for_sql(question, state["schema"], table_name, retry_context=last_error)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            return jsonify({"error": f"Gemini call failed: {e}"}), 500

        try:
            result_df = run_query(sql, table_name)
            last_error = None
            break
        except UnsafeSQLError as e:
            last_error = f"Blocked for safety: {e}"
        except Exception as e:
            last_error = f"SQL execution failed: {e}"

    if last_error:
        return jsonify({
            "error": f"Couldn't generate a working query after retrying. Last issue: {last_error}",
            "sql": sql,
        }), 400

    chart_hint = f"{sid}_{question[:30]}"
    chart_filename = maybe_generate_chart(result_df, filename_hint=chart_hint)
    insight = generate_insight(question, result_df)
    sql_explanation = generate_sql_explanation(sql)

    return jsonify({
        "sql": sql,
        "columns": result_df.columns.tolist(),
        "rows": result_df.values.tolist(),
        "chart": chart_filename,
        "insight": insight,
        "sql_explanation": sql_explanation,
    })
@app.route("/charts/<path:filename>")
def serve_chart(filename):
    return send_from_directory(CHART_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

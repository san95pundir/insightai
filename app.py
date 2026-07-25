import os
import sqlite3
import pandas as pd
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename
from google import genai

# ---------------------------------------------------------------------------
# Reuses the same logic as insightai.py (load_csv_to_sqlite, ask_gemini_for_sql,
# run_query, maybe_generate_chart) but wrapped behind Flask routes instead of
# a CLI loop.
# ---------------------------------------------------------------------------

app = Flask(__name__)

UPLOAD_DIR = "uploads"
CHART_DIR = "charts"
DB_PATH = "data.db"
TABLE_NAME = "data_table"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

# Keep schema in memory between /upload and /ask calls (single-user, simple case)
STATE = {"schema": None, "table_name": TABLE_NAME}


def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def load_csv_to_sqlite(csv_path, table_name=TABLE_NAME, db_path=DB_PATH):
    df = pd.read_csv(csv_path)
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()

    schema_lines = []
    for col, dtype in df.dtypes.items():
        schema_lines.append(f"{col} ({dtype})")
    schema_str = f"Table: {table_name}\nColumns: " + ", ".join(schema_lines)

    preview_rows = df.head(5).astype(str).values.tolist()
    row_count = len(df)

    return schema_str, list(df.columns), preview_rows, row_count


def ask_gemini_for_sql(question, schema, table_name=TABLE_NAME):
    client = get_gemini_client()
    if client is None:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Run: $env:GEMINI_API_KEY='your-key-here' "
            "then restart the Flask server."
        )

    prompt = f"""You are a SQL generator. Given this table schema:

{schema}

Write a single SQLite SELECT query that answers this question:
"{question}"

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
    # Clean up in case the model wraps it in markdown anyway
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


def validate_sql_is_safe(sql):
    """
    Enforces that the SQL is a single, read-only SELECT statement.
    Raises UnsafeSQLError if the query looks like it could modify data,
    contains multiple statements, or isn't a SELECT at all.
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
    import re
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            raise UnsafeSQLError(f"Query contains a disallowed keyword: {keyword.upper()}")

    return cleaned


def run_query(sql, db_path=DB_PATH):
    # Validate first -- reject anything that isn't a clean, single SELECT.
    safe_sql = validate_sql_is_safe(sql)

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

    filename = secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)

    try:
        schema, columns, preview_rows, row_count = load_csv_to_sqlite(save_path)
    except Exception as e:
        return jsonify({"error": f"Failed to load CSV: {e}"}), 400

    STATE["schema"] = schema

    return jsonify({
        "message": "File uploaded and loaded.",
        "schema": schema,
        "columns": columns,
        "preview_rows": preview_rows,
        "row_count": row_count,
        "filename": filename,
    })


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "No question provided"}), 400

    if not STATE["schema"]:
        return jsonify({"error": "No CSV uploaded yet. Upload a file first."}), 400

    try:
        sql = ask_gemini_for_sql(question, STATE["schema"])
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Gemini call failed: {e}"}), 500

    try:
        result_df = run_query(sql)
    except UnsafeSQLError as e:
        # This query was blocked by our safety layer -- log it conceptually
        # as a security event, and never execute it.
        return jsonify({
            "error": f"Blocked for safety: {e}",
            "sql": sql,
        }), 400
    except Exception as e:
        return jsonify({"error": f"SQL execution failed: {e}", "sql": sql}), 400

    chart_filename = maybe_generate_chart(result_df, filename_hint=question[:30])

    return jsonify({
        "sql": sql,
        "columns": result_df.columns.tolist(),
        "rows": result_df.values.tolist(),
        "chart": chart_filename,
    })


@app.route("/charts/<path:filename>")
def serve_chart(filename):
    return send_from_directory(CHART_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

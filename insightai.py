"""
InsightAI (CLI edition) — Ask your business data questions in plain English.

How it works:
1. Load a CSV into a SQLite database.
2. Take a plain-English question from the user.
3. Ask Gemini to translate that question into a SQL query (schema-aware).
4. Run the SQL against SQLite.
5. Print the result.

Setup:
1. Get a free Gemini API key: https://aistudio.google.com/apikey
2. Set it as an environment variable before running:
     export GEMINI_API_KEY="your-key-here"
3. Run:
     python insightai.py sales.csv
"""

import os
import sys
import re
import sqlite3
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # no GUI needed, just save to file
import matplotlib.pyplot as plt

try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def load_csv_to_sqlite(csv_path, db_path="data.db", table_name="data"):
    """Read a CSV and load it into a SQLite table. Returns the schema as a string."""
    df = pd.read_csv(csv_path)
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)

    schema = f"Table `{table_name}` with columns: "
    schema += ", ".join(f"{col} ({str(dtype)})" for col, dtype in df.dtypes.items())
    return conn, schema, table_name


def ask_gemini_for_sql(question, schema, table_name):
    """Send the question + schema to Gemini and get back a single SQL query."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No GEMINI_API_KEY found. Set it with: export GEMINI_API_KEY='your-key'"
        )

    client = genai.Client(api_key=api_key)

    prompt = f"""You are a SQL generator for a SQLite database.

Schema: {schema}
Table name: {table_name}

Convert the following question into a single valid SQLite SQL query.
Rules:
- Return ONLY the SQL query, no explanation, no markdown formatting, no backticks.
- Use the exact table and column names from the schema.
- The query must be read-only (SELECT only).

Question: {question}

SQL:"""

    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
    )
    sql = response.text.strip()

    # Defensive cleanup in case the model wraps it in markdown anyway
    sql = sql.replace("```sql", "").replace("```", "").strip()
    return sql


def run_query(conn, sql):
    """Execute the SQL and return results as a DataFrame."""
    return pd.read_sql_query(sql, conn)


def maybe_generate_chart(result, question, output_dir="charts"):
    """
    If the result looks chartable (one label column + one numeric column,
    more than one row), save a bar chart as a PNG. Returns the file path,
    or None if a chart doesn't make sense for this result.
    """
    if len(result) < 2:
        return None  # a single value (e.g. "total revenue: 500000") isn't a chart

    numeric_cols = result.select_dtypes(include="number").columns.tolist()
    label_cols = result.select_dtypes(exclude="number").columns.tolist()

    if not numeric_cols or not label_cols:
        return None  # need at least one label column and one numeric column

    label_col = label_cols[0]
    value_col = numeric_cols[0]

    os.makedirs(output_dir, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", question.lower()).strip("_")[:40]
    filepath = os.path.join(output_dir, f"{safe_name}.png")

    plt.figure(figsize=(7, 4.5))
    plt.bar(result[label_col].astype(str), result[value_col], color="#2E4057")
    plt.xlabel(label_col)
    plt.ylabel(value_col)
    plt.title(question)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(filepath, dpi=120)
    plt.close()

    return filepath


def main():
    if len(sys.argv) < 2:
        print("Usage: python insightai.py <path_to_csv>")
        sys.exit(1)

    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print(f"Loading {csv_path} into SQLite...")
    conn, schema, table_name = load_csv_to_sqlite(csv_path)
    print(f"Loaded. Schema detected:\n  {schema}\n")

    if not GENAI_AVAILABLE:
        print("google-genai is not installed. Run: pip install google-genai")
        sys.exit(1)

    print("Ask questions about your data in plain English. Type 'exit' to quit.\n")

    while True:
        question = input("Ask> ").strip()
        if question.lower() in ("exit", "quit"):
            break
        if not question:
            continue

        try:
            sql = ask_gemini_for_sql(question, schema, table_name)
            print(f"\nGenerated SQL:\n  {sql}\n")

            result = run_query(conn, sql)
            print("Result:")
            print(result.to_string(index=False))

            chart_path = maybe_generate_chart(result, question)
            if chart_path:
                print(f"Chart saved: {chart_path}")
            print()
        except Exception as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    main()

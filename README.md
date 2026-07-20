# InsightAI (CLI) — Ask your data questions in plain English

Upload a CSV, ask a question in plain English (e.g. "Which product sold the most?"),
and this tool converts it to SQL, runs it, and shows you the answer.

## Setup

1. Install dependencies:
   ```
   pip install pandas google-genai matplotlib
   ```

2. Get a free Gemini API key: https://aistudio.google.com/apikey

3. Set it as an environment variable:
   ```
   export GEMINI_API_KEY="your-key-here"
   ```
   (On Windows: `set GEMINI_API_KEY=your-key-here`)

## Run

```
python insightai.py sales.csv
```

Then ask questions like:
- "Which product sold the most?"
- "Total revenue per month"
- "Who is the top customer by spend?"

Type `exit` to quit.

## How it works

1. `load_csv_to_sqlite()` reads the CSV with pandas and loads it into a local SQLite table.
2. Your question + the table's schema get sent to Gemini with a prompt that asks it
   to return ONLY a SQL query.
3. That SQL runs against SQLite with pandas' `read_sql_query`.
4. The result prints as a table.
5. If the result has multiple rows and a numeric column, a bar chart is
   automatically saved as a PNG in a `charts/` folder.

## Notes

- This is intentionally a command-line tool, not a web app — it's scoped to be
  finishable in a few hours while still demonstrating the full pipeline:
  Python, SQL, and a real GenAI API integration.
- If you have more time later, the natural next steps are: wrap this in a Flask
  API, add a React frontend for upload + chat, and add chart generation
  (matplotlib/Plotly) on the result DataFrame.

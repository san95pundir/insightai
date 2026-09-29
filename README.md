# InsightAI — Ask Your Data Questions in Plain English

InsightAI is an AI-assisted analytics platform that allows users to upload a structured CSV dataset and ask analytical questions in plain English instead of manually writing SQL.

For example, a user can upload a customer dataset and ask:

> "Which contract type has the highest churn?"

InsightAI uses the uploaded dataset schema to generate SQL, validates the query, executes it safely against SQLite, and presents the result along with a plain-English SQL explanation and a short Analyst's Note.

The project is designed as a practical analytics workflow combining **Python, SQL, data profiling, AI-assisted querying, and business interpretation**.

---

## Live Demo

[**Try InsightAI Live →**](https://insightai-4lor.onrender.com/)

*Hosted on Render's free tier — the first request after inactivity may take some time to wake up.*

---

## Screenshots

### 1. Upload Dataset

Users can upload a CSV file using the drag-and-drop interface or file browser.

![Upload Dataset](screenshots/01-upload.png)

---

### 2. Dataset Preview

After uploading, InsightAI displays the dataset row count, column count, and a preview of the uploaded data.

![Dataset Preview](screenshots/02-preview.png)

---

### 3. Automated EDA / Dataset Summary

InsightAI automatically generates a quick dataset summary after upload.

For numeric columns, it displays:
- Mean
- Median
- Minimum
- Maximum
- Missing values

For categorical columns, it displays:
- Top values
- Value counts
- Missing values

![Automated EDA Summary](screenshots/03-eda-summary.png)

---

### 4. Suggested Analytical Questions

InsightAI generates dataset-specific analytical questions from the uploaded schema. These are displayed as clickable suggestions to help users begin their analysis.

![Suggested Questions](screenshots/04-suggested-questions.png)

---

### 5. Natural Language → SQL

Users can ask questions in plain English instead of manually writing SQL.

Example:

> "Which contract type has the highest churn?"

InsightAI generates a SQL query based on the uploaded dataset.

![Natural Language to SQL](screenshots/05-natural-language-sql.png)

---

### 6. Query Result

The generated SQL is executed safely against the uploaded dataset and the resulting data is displayed in a table.

![Query Result](screenshots/06-query-result.png)

---

### 7. SQL Explanation

InsightAI provides a plain-English explanation of the generated SQL query so users can understand what the query is doing.

![SQL Explanation](screenshots/07-sql-explanation.png)

---

### 8. Analyst's Note

The application generates a short business interpretation of the result instead of showing only raw numbers.

This helps connect technical query results with a business-oriented analytical observation.

![Analyst's Note](screenshots/08-analyst-note.png)

---

### 9. Session History

Every question asked during the current browser session is stored in Session History.

Clicking an earlier question restores that interaction in the main result area, including its question, SQL, result, explanation, and Analyst's Note.

![Session History](screenshots/09-session-history.png)

---

## Features

- **Natural Language → SQL** — ask questions in plain English and receive a validated, read-only SQL query and its result.
- **Session-based dataset handling** — uploaded datasets and query state are associated with the current user session.
- **Dataset profiling on upload** — row count, column count, and a dataset preview are shown immediately after upload.
- **Automated EDA summary** — numeric and categorical columns are summarized automatically with useful statistics and missing-value counts.
- **Suggested questions** — dataset-specific analytical questions are generated from the uploaded schema and displayed as clickable chips.
- **SQL validation and read-only execution** — generated SQL is checked before execution and destructive database operations are blocked.
- **SQL explanation** — a plain-English explanation of the generated query helps users understand the SQL.
- **Analyst's Note** — a short business interpretation is generated from the query result.
- **CSV export** — users can download query results as a CSV file directly from the browser.
- **Error recovery** — temporary Gemini/API failures are retried before an error is shown to the user.
- **Session history** — previous questions can be selected and their complete interactions restored in the main result area.

---

## How It Works

### 1. CSV → SQLite

The uploaded CSV is read using Pandas and loaded into SQLite.

At the same time, InsightAI extracts information about the dataset, including its columns and data types, to build a schema representation for query generation.

---

### 2. Dataset Profiling

Before the user asks a question, InsightAI automatically provides:

- Row count
- Column count
- Dataset preview
- Numeric column statistics
- Categorical top values
- Missing-value counts

This gives the user an initial understanding of the dataset before querying it.

---

### 3. Suggested Questions

The dataset schema is used to generate short analytical questions that can be answered using the uploaded table.

These questions are displayed as clickable chips in the interface.

---

### 4. Question → SQL

The user's natural-language question and the dataset schema are sent to Gemini.

The model is instructed to generate SQL for the uploaded dataset.

---

### 5. SQL Safety Layer

The generated SQL is not executed blindly.

InsightAI validates the query before execution.

The validation layer checks that:

- The query is a `SELECT` statement.
- Only a single SQL statement is executed.
- Destructive operations are blocked.
- The query refers to the expected uploaded dataset table.

The SQLite database is also opened in read-only mode during query execution.

---

### 6. Query Execution

After validation, the SQL query is executed against the uploaded dataset using SQLite and Pandas.

The resulting rows and columns are returned to the frontend.

---

### 7. Result + Explanation

The query result is displayed as a table.

InsightAI also provides:

- A plain-English explanation of the SQL
- A short Analyst's Note interpreting the result

This helps bridge the gap between technical SQL output and business understanding.

---

### 8. Session History

Each question and its complete response are stored in browser memory during the current session.

Users can select previous questions from Session History to restore the corresponding interaction in the main result area.

Refreshing the page clears this browser-side history.

---

## Business Analyst Workflow

InsightAI is designed around a practical analytics workflow that can be used by someone who wants to explore data without writing every SQL query manually.

```text
Upload Dataset
      ↓
Preview & Dataset Profiling
      ↓
Automated EDA Summary
      ↓
Suggested Analytical Questions
      ↓
Ask Question in Plain English
      ↓
AI-Generated SQL
      ↓
SQL Validation
      ↓
Safe Query Execution
      ↓
Result Table
      ↓
SQL Explanation
      ↓
Analyst's Note
      ↓
Session History

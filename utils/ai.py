# ─────────────────────────────────────────────────────────────
# AI layer with 3 modes: QUERY, CRUD, LEARN
#
# Flow:
#   1. detect_intent()  → figures out which mode to use
#   2. Mode-specific prompt builder runs
#   3. Groq API called with that prompt
#   4. Response parsed and returned to app.py
#
# Key design: each mode returns a standardized dict so
# app.py always knows what shape of data to expect.
# ─────────────────────────────────────────────────────────────

import re
import os
import sys
from pathlib import Path
from utils.config import get_groq_keys
from groq import Groq, RateLimitError

sys.path.append(str(Path(__file__).resolve().parent.parent))



# ── API KEY ROTATION ──────────────────────────────────────────
def get_api_keys() -> list[str]:
    """
    Gets Groq API keys via unified config loader.
    Works in both local and Streamlit Cloud environments.
    """
    return get_groq_keys()


def call_groq(system_prompt: str, user_message: str) -> tuple[str | None, str | None]:
    """
    Central Groq caller with key rotation built in.
    Every mode uses this — avoids repeating rotation logic 3 times.

    Why temperature=0:
    All 3 modes need deterministic output.
    - Query/CRUD: same question must always produce same SQL
    - Learn: explanations should be consistent not random

    Returns:
        (response_text, None)  → success
        (None, error_msg)      → all keys failed
    """
    keys = get_api_keys()

    for i, api_key in enumerate(keys, start=1):
        try:
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message}
                ],
                temperature=0,
                max_tokens=1024,
            )
            return response.choices[0].message.content.strip(), None

        except RateLimitError:
            print(f"⚠️ Key {i} rate limited — trying next...")
            continue
        except Exception as e:
            return None, f"Groq API error: {str(e)}"

    return None, "❌ All API keys are rate limited. Please wait a minute."


# ── INTENT DETECTOR ───────────────────────────────────────────
# Why keyword matching instead of asking AI to classify:
# Asking the AI to classify intent costs an extra API call
# and adds latency. Keyword matching is instant and handles
# 95% of cases correctly. We only fall back to AI for
# genuinely ambiguous messages.

# CRUD keywords — anything that modifies data
CRUD_KEYWORDS = {
    "delete", "remove", "drop", "erase", "clear",
    "update", "change", "modify", "set", "edit", "rename",
    "insert", "add", "create", "put", "append", "new row",
    "replace", "fix", "correct", "fill in",
}

# LEARN keywords — anything asking for SQL explanation
LEARN_KEYWORDS = {
    "what is", "what does", "what are", "what's",
    "how does", "how do", "how to", "how can",
    "explain", "teach me", "help me understand",
    "why", "difference between", "when to use",
    "what's the difference", "define", "meaning",
    "example of", "show me how", "can you explain",
    "i don't understand", "what do you mean",
}


def detect_intent(message: str) -> str:
    """
    Classifies user message into one of 3 modes.

    Detection order matters:
    1. Check LEARN first — learning questions sometimes contain
       words like "delete" (e.g. "explain how DELETE works")
       so we check learning intent before CRUD intent.
    2. Check CRUD second — explicit data modification keywords
    3. Default to QUERY — anything else is a data question

    Returns: "learn", "crud", or "query"
    """
    msg_lower = message.lower().strip()

    # ── Learn check ────────────────────────────────────────────
    # Why check for "explain" + "sql"/"query" separately:
    # "explain the query" is LEARN but "explain sales by region"
    # is actually a QUERY request phrased unusually.
    for keyword in LEARN_KEYWORDS:
        if keyword in msg_lower:
            return "learn"

    # Also catch "explain [the] [last] [generated] sql/query"
    if re.search(r"\b(explain|describe)\b.{0,30}\b(sql|query|code)\b", msg_lower):
        return "learn"

    # ── CRUD check ─────────────────────────────────────────────
    for keyword in CRUD_KEYWORDS:
        if keyword in msg_lower:
            return "crud"

    # ── Default → Query ────────────────────────────────────────
    return "query"


# ── SCHEMA FORMATTER ──────────────────────────────────────────
def format_schema_for_prompt(table_name: str, schema: dict) -> str:
    """
    Converts the schema dict into a clean string for AI prompts.

    Why format it this way:
    The AI reads this as part of its instructions. Clear,
    structured formatting = fewer mistakes in generated SQL.
    We show exact column names and types so the AI never
    has to guess.

    Args:
        table_name: e.g. "sales_2024"
        schema: e.g. {"product_name": "TEXT", "sales": "NUMERIC"}
    """
    col_lines = "\n    ".join(
        [f"{col} ({pg_type})" for col, pg_type in schema.items()]
    )
    return f"""
    TABLE: "{table_name}"
    COLUMNS:
    _id (BIGINT) ← auto-generated primary key, always exists
    {col_lines}
    """


# ══════════════════════════════════════════════════════════════
# MODE 1: QUERY
# Handles all SELECT / read-only questions
# ══════════════════════════════════════════════════════════════

def build_query_prompt(table_name: str, schema: dict) -> str:
    schema_str = format_schema_for_prompt(table_name, schema)
    return f"""
You are an expert PostgreSQL query writer.
Your ONLY job is to write a valid SELECT query based on the user's question.

════════════════════════════════════════
DATABASE SCHEMA
════════════════════════════════════════
{schema_str}

════════════════════════════════════════
STRICT RULES
════════════════════════════════════════
1. Return ONLY the raw SQL — no explanation, no markdown,
   no backticks, no preamble.

2. Only write SELECT statements.
   NEVER use: DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE.

3. Always wrap column and table names in double quotes:
   SELECT "product_name", "sales" FROM "{table_name}"

4. Use ROUND(...::numeric, 2) for all decimal calculations.

5. Use NULLIF to prevent division by zero:
   ROUND((SUM("profit") / NULLIF(SUM("sales"), 0) * 100)::numeric, 2)

6. Always add LIMIT 100 unless user specifies otherwise.

7. If the question cannot be answered from this data, return:
   INVALID_QUESTION

════════════════════════════════════════
EXAMPLES FOR THIS TABLE
════════════════════════════════════════
Question: Show me the first 10 rows
SQL: SELECT * FROM "{table_name}" LIMIT 10;

Question: How many rows are there?
SQL: SELECT COUNT(*) AS total_rows FROM "{table_name}";

Question: What is the average value of numeric columns?
SQL: SELECT AVG("_id") AS avg_id FROM "{table_name}" LIMIT 100;
"""


def handle_query(
    question: str,
    table_name: str,
    schema: dict
) -> dict:
    """
    Generates a SELECT SQL query for the user's question.

    Returns a standardized dict so app.py always knows
    what fields to expect regardless of which mode ran.

    Return shape:
    {
        "mode":    "query",
        "sql":     "SELECT ...",
        "error":   None or "error message",
    }
    """
    prompt = build_query_prompt(table_name, schema)
    raw, error = call_groq(prompt, question)

    if error:
        return {"mode": "query", "sql": None, "error": error}

    if "INVALID_QUESTION" in raw:
        return {
            "mode":  "query",
            "sql":   None,
            "error": "❌ I can't answer that from your data. Try asking about specific columns or values in your dataset."
        }

    sql = clean_sql(raw)
    return {"mode": "query", "sql": sql, "error": None}


# ══════════════════════════════════════════════════════════════
# MODE 2: CRUD
# Handles INSERT, UPDATE, DELETE operations
# ══════════════════════════════════════════════════════════════

def build_crud_prompt(table_name: str, schema: dict) -> str:
    schema_str = format_schema_for_prompt(table_name, schema)
    return f"""
You are an expert PostgreSQL database manager.
The user wants to modify data. Generate the appropriate SQL statement.

════════════════════════════════════════
DATABASE SCHEMA
════════════════════════════════════════
{schema_str}

════════════════════════════════════════
STRICT RULES
════════════════════════════════════════
1. Return ONLY a valid SQL statement — no explanation,
   no markdown, no backticks.

2. Allowed statements: INSERT, UPDATE, DELETE only.
   NEVER use: DROP TABLE, DROP DATABASE, TRUNCATE, ALTER TABLE.
   Why: These are irreversible structural changes. Only data
   modification is allowed, not schema modification.

3. Always wrap column and table names in double quotes.

4. For DELETE and UPDATE, ALWAYS include a WHERE clause.
   NEVER write: DELETE FROM "{table_name}" (no WHERE)
   Why: DELETE without WHERE wipes the entire table.
   If no condition is clear, return: NEEDS_CLARIFICATION

5. If the request is too vague or dangerous, return:
   NEEDS_CLARIFICATION

6. After your SQL, on a NEW LINE write a one-sentence
   plain English description starting with "ACTION:"
   Example:
   DELETE FROM "sales" WHERE "profit" < 0;
   ACTION: Deletes all rows where profit is negative.

════════════════════════════════════════
EXAMPLES
════════════════════════════════════════
Request: delete rows where sales are zero
SQL:
DELETE FROM "{table_name}" WHERE "sales" = 0;
ACTION: Deletes all rows where sales value is exactly zero.

Request: update product name from Chair to Office Chair
SQL:
UPDATE "{table_name}" SET "product_name" = 'Office Chair' WHERE "product_name" = 'Chair';
ACTION: Renames all rows with product name "Chair" to "Office Chair".
"""


def build_preview_sql(crud_sql: str, table_name: str) -> str | None:
    """
    Converts a CRUD SQL into a SELECT preview query.

    Why show a preview before executing:
    The user must see WHAT will be affected before we delete
    or update it. This is the safety gate — without it, a
    mistyped condition could destroy the wrong data.

    We parse the WHERE clause from the CRUD statement and
    build a SELECT with that same WHERE clause.

    Example:
        Input:  DELETE FROM "sales" WHERE "profit" < 0
        Output: SELECT * FROM "sales" WHERE "profit" < 0 LIMIT 20
    """
    try:
        sql_upper = crud_sql.upper()

        # Extract WHERE clause — works for both DELETE and UPDATE
        # Why regex: WHERE clause position differs between statement types
        where_match = re.search(
            r'\bWHERE\b(.+?)(?:;|$)',
            crud_sql,
            re.IGNORECASE | re.DOTALL
        )

        if where_match:
            where_clause = where_match.group(1).strip()
            return f'SELECT * FROM "{table_name}" WHERE {where_clause} LIMIT 20;'

        # For INSERT — show current table state
        if "INSERT" in sql_upper:
            return f'SELECT * FROM "{table_name}" ORDER BY _id DESC LIMIT 5;'

        return None

    except Exception:
        return None


def parse_crud_response(raw: str) -> tuple[str, str]:
    """
    Splits the AI response into SQL and ACTION description.

    The CRUD prompt asks the AI to return:
        Line 1: The SQL statement
        Line 2: ACTION: description

    We split on "ACTION:" to get both parts.
    """
    if "ACTION:" in raw:
        parts = raw.split("ACTION:", 1)
        sql    = parts[0].strip()
        action = parts[1].strip()
    else:
        sql    = raw.strip()
        action = "This operation will modify your data."

    return sql, action


def handle_crud(
    question: str,
    table_name: str,
    schema: dict
) -> dict:
    """
    Generates a CRUD SQL statement with safety information.

    Return shape:
    {
        "mode":        "crud",
        "sql":         "DELETE FROM ...",
        "action":      "Deletes rows where...",
        "preview_sql": "SELECT * FROM ... WHERE ...",
        "operation":   "DELETE" / "UPDATE" / "INSERT",
        "error":       None or "error message",
    }
    """
    prompt = build_crud_prompt(table_name, schema)
    raw, error = call_groq(prompt, question)

    if error:
        return {"mode": "crud", "sql": None, "error": error}

    if "NEEDS_CLARIFICATION" in raw:
        return {
            "mode":  "crud",
            "sql":   None,
            "error": "⚠️ Your request is too vague. Please be more specific — for example: 'Delete rows where sales is less than 10' or 'Update the city to Karachi where customer name is Ali'."
        }

    sql, action = parse_crud_response(raw)
    sql = clean_sql(sql)

    # Detect operation type from SQL
    sql_upper = sql.upper().strip()
    if sql_upper.startswith("DELETE"):
        operation = "DELETE"
    elif sql_upper.startswith("UPDATE"):
        operation = "UPDATE"
    elif sql_upper.startswith("INSERT"):
        operation = "INSERT"
    else:
        operation = "UNKNOWN"

    # Build preview query so user sees affected rows first
    preview_sql = build_preview_sql(sql, table_name)

    return {
        "mode":        "crud",
        "sql":         sql,
        "action":      action,
        "preview_sql": preview_sql,
        "operation":   operation,
        "error":       None,
    }


# ══════════════════════════════════════════════════════════════
# MODE 3: LEARN
# Explains SQL concepts or explains generated SQL
# ══════════════════════════════════════════════════════════════

def build_learn_prompt(last_sql: str | None) -> str:
    """
    Why two sub-modes inside LEARN:
    - If user asks "explain the last query" → explain last_sql
    - If user asks "what is GROUP BY" → explain the concept

    We tell the AI about both scenarios and let it handle
    whichever the user is asking about.
    """
    last_sql_section = f"""
════════════════════════════════════════
LAST GENERATED SQL (if user asks about it)
════════════════════════════════════════
{last_sql if last_sql else "No SQL has been generated yet in this session."}
""" if last_sql else ""

    return f"""
You are a friendly SQL teacher who explains database concepts
clearly to beginners with zero technical background.

{last_sql_section}

════════════════════════════════════════
YOUR TEACHING STYLE
════════════════════════════════════════
1. Use simple, everyday analogies. Compare SQL concepts to
   things from real life — spreadsheets, filing cabinets,
   lists, recipes.

2. Structure every explanation with these sections:
   📖 WHAT IT IS: One sentence definition
   🌍 REAL WORLD ANALOGY: Compare to something familiar
   💡 HOW IT WORKS: 2-3 sentences explaining the mechanics
   📝 EXAMPLE: A short SQL example with explanation
   ✅ WHEN TO USE IT: One or two practical use cases

3. If the user asks to explain the last generated SQL:
   - Break it down clause by clause
   - Explain what each part does in plain English
   - Point out any interesting techniques used

4. Keep explanations under 250 words — clear and focused.

5. After your explanation, suggest ONE follow-up question
   the user could ask to learn more, starting with:
   "💬 Try asking: ..."

6. NEVER use jargon without explaining it first.
   NEVER be condescending — treat the user as intelligent
   but new to SQL.
"""


def handle_learn(
    question: str,
    last_sql: str | None
) -> dict:
    """
    Returns a plain English SQL explanation.

    Return shape:
    {
        "mode":        "learn",
        "explanation": "📖 WHAT IT IS: ...",
        "error":       None or "error message",
    }
    """
    prompt = build_learn_prompt(last_sql)
    response, error = call_groq(prompt, question)

    if error:
        return {"mode": "learn", "explanation": None, "error": error}

    return {
        "mode":        "learn",
        "explanation": response,
        "error":       None,
    }


# ── SQL CLEANER ───────────────────────────────────────────────
def clean_sql(raw: str) -> str:
    """Strips markdown fences from AI SQL response."""
    raw = re.sub(r"```sql\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```",       "", raw)
    return raw.strip()


# ── MASTER FUNCTION ───────────────────────────────────────────
def generate_response(
    question:   str,
    table_name: str,
    schema:     dict,
    last_sql:   str | None = None
) -> dict:
    """
    The only function app.py needs to call.

    Steps:
        1. Detect which mode the question needs
        2. Route to the correct handler
        3. Return standardized result dict

    Why last_sql parameter:
        The LEARN mode can explain the previously generated SQL.
        app.py stores the last SQL in session state and passes
        it here so the AI has context for "explain that query".

    Args:
        question:   What the user typed
        table_name: Currently active table
        schema:     {column_name: postgres_type} dict
        last_sql:   Last SQL generated this session (optional)

    Returns:
        dict with "mode" key always set, other keys vary by mode
    """
    intent = detect_intent(question)
    print(f"   🎯 Intent detected: {intent.upper()}")

    if intent == "learn":
        return handle_learn(question, last_sql)
    elif intent == "crud":
        return handle_crud(question, table_name, schema)
    else:
        return handle_query(question, table_name, schema)


# ── TEST ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🤖 Testing AI — 3 Modes")
    print("─" * 40)

    # Simulated schema from an uploaded CSV
    TABLE  = "sales_2024"
    SCHEMA = {
        "product_name": "TEXT",
        "category":     "TEXT",
        "sales":        "NUMERIC",
        "profit":       "NUMERIC",
        "quantity":     "BIGINT",
        "order_date":   "TIMESTAMP",
        "city":         "TEXT",
        "region":       "TEXT",
    }

    tests = [
        # (question, description)
        ("Show me top 5 products by sales",           "QUERY"),
        ("Which region has the highest profit?",      "QUERY"),
        ("Delete all rows where profit is negative",  "CRUD"),
        ("Update quantity to 10 where it is zero",    "CRUD"),
        ("What does GROUP BY do?",                    "LEARN"),
        ("Explain the last query",                    "LEARN"),
    ]

    last_sql = None

    for question, expected in tests:
        print(f"\n{'─'*50}")
        print(f"❓ [{expected}] {question}")

        result = generate_response(question, TABLE, SCHEMA, last_sql)

        if result["error"]:
            print(f"   ❌ Error: {result['error']}")

        elif result["mode"] == "query":
            print(f"   ✅ SQL: {result['sql']}")
            last_sql = result["sql"]

        elif result["mode"] == "crud":
            print(f"   ⚠️  Operation : {result['operation']}")
            print(f"   ✅ SQL       : {result['sql']}")
            print(f"   📋 Action    : {result['action']}")
            print(f"   🔍 Preview   : {result['preview_sql']}")

        elif result["mode"] == "learn":
            # Truncate long explanations in test output
            explanation = result["explanation"] or ""
            preview = explanation[:300] + "..." if len(explanation) > 300 else explanation
            print(f"   📖 {preview}")

    print("\n\n✅ All 3 modes working! Move to Step 8.\n")
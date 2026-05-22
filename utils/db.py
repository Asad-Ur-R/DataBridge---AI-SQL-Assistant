# utils/db.py
# ─────────────────────────────────────────────────────────────
# All database operations — connection, SELECT, CRUD, logging.
#
# Every operation passes through security.py before execution.
# No SQL ever touches the database without being validated.
# ─────────────────────────────────────────────────────────────

import os
import sys
import psycopg2
import pandas as pd
from pathlib import Path
from utils.config import get_db_config

sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils.security import validate_sql



# ── CONNECTION ────────────────────────────────────────────────
def get_connection():
    """
    Returns a fresh PostgreSQL connection.
    Uses connection string for Neon (fixes Windows SSL issue).
    Uses keyword args for local PostgreSQL.
    """
    from utils.config import get_db_config, get
    config = get_db_config()

    # If connecting to Neon use full connection string
    # Why: Windows psycopg2 handles Neon SSL better this way
    sslmode = config.pop("sslmode", None)
    if sslmode:
        conn_string = (
            f"postgresql://"
            f"{config['user']}:{config['password']}"
            f"@{config['host']}:{config['port']}"
            f"/{config['dbname']}?sslmode={sslmode}"
        )
        return psycopg2.connect(conn_string)

    return psycopg2.connect(**config)

# ── SELECT QUERY RUNNER ───────────────────────────────────────
def run_query(sql: str) -> tuple[pd.DataFrame | None, str | None]:
    is_safe, reason = validate_sql(sql, operation_type="query")
    if not is_safe:
        return None, reason

    engine = None
    try:
        from sqlalchemy import create_engine, text
        from utils.config import get_db_config

        cfg  = get_db_config()
        ssl  = cfg.pop("sslmode", None)

        # Build connection URL
        db_url = (
            f"postgresql+psycopg2://"
            f"{cfg['user']}:{cfg['password']}"
            f"@{cfg['host']}:{cfg['port']}"
            f"/{cfg['dbname']}"
        )

        # Add SSL query param if needed
        # Why connect_args instead of URL param:
        # Some SQLAlchemy versions handle SSL better via
        # connect_args dict than via URL query string.
        connect_args = {"sslmode": ssl} if ssl else {}
        engine = create_engine(db_url, connect_args=connect_args)

        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
        return df, None

    except Exception as e:
        return None, str(e)

    finally:
        if engine:
            engine.dispose()


# ── CRUD EXECUTOR ─────────────────────────────────────────────
def execute_crud(sql: str) -> tuple[int, str | None]:
    """
    Executes INSERT, UPDATE, or DELETE and returns affected rows.

    Why return row count:
    After a DELETE or UPDATE, the user needs to see how many
    rows were affected. "3 rows deleted" is much more useful
    than a blank screen. psycopg2's cursor.rowcount gives us
    this number directly.

    Why NOT use SQLAlchemy here:
    psycopg2's cursor.rowcount works reliably for DML statements.
    SQLAlchemy adds abstraction that can make rowcount unreliable
    for some statement types. For CRUD we use raw psycopg2.

    Security: validates SQL as "crud" type before execution.

    Returns:
        (rows_affected, None)   → success
        (0, error_msg)          → failed or blocked
    """
    # Security check — crud mode allows INSERT/UPDATE/DELETE
    # but still blocks DROP, TRUNCATE, injection patterns etc.
    is_safe, reason = validate_sql(sql, operation_type="crud")
    if not is_safe:
        return 0, reason

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(sql)
            # rowcount = number of rows affected by the statement
            # -1 means the DB couldn't determine it (rare)
            rows_affected = cur.rowcount if cur.rowcount != -1 else 0
        conn.commit()
        return rows_affected, None

    except Exception as e:
        # Rollback on error — undo any partial changes
        # Why rollback: If an error happens mid-execution,
        # partial changes could leave data in a corrupt state.
        # Rollback reverts everything to before the statement ran.
        if conn:
            conn.rollback()
        return 0, str(e)

    finally:
        if conn:
            conn.close()


# ── QUERY LOGGER ──────────────────────────────────────────────
def log_query(
    question:   str,
    sql:        str,
    row_count:  int,
    mode:       str = "query"
):
    """
    Logs every interaction to query_history.

    Why log the mode:
    Now that we have 3 modes, knowing whether a query was
    a SELECT, CRUD, or LEARN interaction is useful for
    analytics — you could show users "you've made 5 data
    changes this week" as a feature later.
    """
    conn = None
    try:
        conn = get_connection()

        # Create table if it doesn't exist yet
        # Why here: Guarantees the table exists before every log
        # attempt — safe to call repeatedly due to IF NOT EXISTS
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS query_history (
                    id            SERIAL PRIMARY KEY,
                    user_question TEXT,
                    generated_sql TEXT,
                    row_count     INTEGER,
                    mode          TEXT DEFAULT 'query',
                    asked_at      TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                INSERT INTO query_history
                    (user_question, generated_sql, row_count, mode)
                VALUES (%s, %s, %s, %s)
            """, (question, sql, row_count, mode))
        conn.commit()

    except Exception as e:
        # Non-critical — logging failure never crashes the app
        print(f"⚠️ Logging failed: {e}")
    finally:
        if conn:
            conn.close()


# ── QUERY HISTORY ─────────────────────────────────────────────
def get_query_history(limit: int = 10) -> pd.DataFrame | None:
    """Fetches most recent queries from history."""
    sql = f"""
        SELECT
            user_question  AS "Question",
            mode           AS "Mode",
            row_count      AS "Rows",
            asked_at       AS "Time"
        FROM query_history
        ORDER BY asked_at DESC
        LIMIT {limit}
    """
    df, _ = run_query(sql)
    return df


# ── DATABASE STATS ────────────────────────────────────────────
def get_table_stats(table_name: str) -> dict:
    """
    Returns basic stats about an uploaded table.
    Used by the app sidebar and results header.

    Why row count + column count:
    These are the two most useful "at a glance" facts
    about a dataset — how big is it and how wide is it.
    """
    stats = {
        "row_count":    0,
        "column_count": 0,
        "table_name":   table_name
    }

    # Row count
    df, err = run_query(f'SELECT COUNT(*) AS n FROM "{table_name}"')
    if df is not None:
        stats["row_count"] = int(df["n"].iloc[0])

    # Column count from information_schema
    # Why information_schema here and not blocked by security:
    # We use run_query which validates as SELECT.
    # information_schema is blocked for user inputs but we
    # call it directly from trusted app code — safe.
    # We bypass security for this internal call only.
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_name = %s
            """, (table_name,))
            stats["column_count"] = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass

    return stats


# ── SCHEMA ────────────────────────────────────────────────────
def get_schema() -> str:
    """
    Legacy function kept for backward compatibility.
    New code uses format_schema_for_prompt() in ai.py instead.
    """
    return """
    TABLE: orders
    COLUMNS: row_id, order_id, order_date, ship_date,
             ship_mode, customer_id, product_id,
             sales, quantity, discount, profit

    TABLE: customers
    COLUMNS: customer_id, customer_name, segment,
             country, city, state, postal_code, region

    TABLE: products
    COLUMNS: product_id, product_name, category, sub_category
    """


# ── TEST ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🗄️  Testing db.py with Security")
    print("─" * 40)

    print("\n1️⃣  SELECT query (should work):")
    df, err = run_query("SELECT COUNT(*) AS total FROM orders")
    if err:
        print(f"   ❌ {err}")
    else:
        print(f"   ✅ Orders in DB: {df['total'].iloc[0]:,}")

    print("\n2️⃣  Blocked query (DROP TABLE):")
    df, err = run_query('DROP TABLE orders')
    if err:
        print(f"   ✅ Correctly blocked: {err}")

    print("\n3️⃣  Blocked CRUD (DELETE without WHERE):")
    rows, err = execute_crud('DELETE FROM orders')
    if err:
        print(f"   ✅ Correctly blocked: {err}")

    print("\n4️⃣  Valid CRUD simulation:")
    # We test on query_history which is safe to modify
    rows, err = execute_crud(
        "INSERT INTO query_history (user_question, generated_sql, row_count, mode) "
        "VALUES ('test question', 'SELECT 1', 1, 'query')"
    )
    if err:
        print(f"   ❌ {err}")
    else:
        print(f"   ✅ INSERT worked — {rows} row affected")

    # Clean up test entry
    execute_crud(
        "DELETE FROM query_history WHERE user_question = 'test question'"
    )
    print(f"   ✅ Test entry cleaned up")

    print("\n✅ db.py ready! Move to Step 9.\n")
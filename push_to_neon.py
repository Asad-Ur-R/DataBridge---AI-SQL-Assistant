# push_to_neon.py
# ─────────────────────────────────────────────────────────────
# One-time script to copy your local database to Neon.
# Run this once before deploying.
# Safe to run again — uses ON CONFLICT DO NOTHING.
# ─────────────────────────────────────────────────────────────

import os
import psycopg2
import pandas as pd
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()


def get_local_conn():
    return psycopg2.connect(
        host     = os.getenv("DB_HOST",     "localhost"),
        port     = os.getenv("DB_PORT",     "5432"),
        dbname   = os.getenv("DB_NAME",     "superstore_db"),
        user     = os.getenv("DB_USER",     "postgres"),
        password = os.getenv("DB_PASSWORD", ""),
    )


def get_neon_conn():
    """
    Connects to Neon using the full connection string directly.

    Why connection string instead of individual params:
    Neon on Windows has SSL handshake issues with psycopg2
    when passing sslmode as a keyword argument. Using the
    full connection string with ?sslmode=require embedded
    lets psycopg2 handle SSL negotiation correctly.
    """
    conn_string = (
        f"postgresql://"
        f"{os.getenv('NEON_USER')}:{os.getenv('NEON_PASSWORD')}"
        f"@{os.getenv('NEON_HOST')}:{os.getenv('NEON_PORT', '5432')}"
        f"/{os.getenv('NEON_NAME')}?sslmode=require"
    )
    return psycopg2.connect(conn_string)


def copy_table(local_conn, neon_conn, table: str, create_sql: str):
    """Copies one table from local PostgreSQL to Neon."""
    print(f"\n📋 Copying table: {table}")

    # Read all data from local
    with local_conn.cursor() as cur:
        cur.execute(f'SELECT * FROM {table}')
        rows    = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

    print(f"   ✅ Read {len(rows):,} rows from local")

    if not rows:
        print(f"   ⚠️  No data to copy")
        return

    # Create table on Neon
    with neon_conn.cursor() as cur:
        cur.execute(create_sql)
    neon_conn.commit()
    print(f"   ✅ Table created on Neon")

    # Bulk insert to Neon
    col_names   = ", ".join([f'"{c}"' for c in columns])
    insert_sql  = f'INSERT INTO {table} ({col_names}) VALUES %s ON CONFLICT DO NOTHING'

    with neon_conn.cursor() as cur:
        execute_values(cur, insert_sql, rows, page_size=500)
    neon_conn.commit()
    print(f"   ✅ {len(rows):,} rows inserted to Neon")


if __name__ == "__main__":
    print("\n🚀 Pushing data to Neon")
    print("─" * 40)

    local = get_local_conn()
    neon  = get_neon_conn()

    try:
        # ── customers ─────────────────────────────────────────
        copy_table(local, neon, "customers", """
            DROP TABLE IF EXISTS customers CASCADE;
            CREATE TABLE customers (
                customer_id   TEXT PRIMARY KEY,
                customer_name TEXT,
                segment       TEXT,
                country       TEXT,
                city          TEXT,
                state         TEXT,
                postal_code   TEXT,
                region        TEXT
            );
        """)

        # ── products ──────────────────────────────────────────
        copy_table(local, neon, "products", """
            DROP TABLE IF EXISTS products CASCADE;
            CREATE TABLE products (
                product_id   TEXT PRIMARY KEY,
                product_name TEXT,
                category     TEXT,
                sub_category TEXT
            );
        """)

        # ── orders ────────────────────────────────────────────
        copy_table(local, neon, "orders", """
            DROP TABLE IF EXISTS orders CASCADE;
            CREATE TABLE orders (
                row_id      INTEGER PRIMARY KEY,
                order_id    TEXT,
                order_date  DATE,
                ship_date   DATE,
                ship_mode   TEXT,
                customer_id TEXT REFERENCES customers(customer_id),
                product_id  TEXT REFERENCES products(product_id),
                sales       NUMERIC(10,2),
                quantity    INTEGER,
                discount    NUMERIC(5,2),
                profit      NUMERIC(10,2)
            );
        """)

        # ── query_history ──────────────────────────────────────
        copy_table(local, neon, "query_history", """
            DROP TABLE IF EXISTS query_history CASCADE;
            CREATE TABLE query_history (
                id            SERIAL PRIMARY KEY,
                user_question TEXT,
                generated_sql TEXT,
                row_count     INTEGER,
                mode          TEXT DEFAULT 'query',
                asked_at      TIMESTAMP DEFAULT NOW()
            );
        """)

        # ── _uploaded_tables ───────────────────────────────────
        copy_table(local, neon, "_uploaded_tables", """
            DROP TABLE IF EXISTS _uploaded_tables CASCADE;
            CREATE TABLE _uploaded_tables (
                id            SERIAL PRIMARY KEY,
                table_name    TEXT NOT NULL,
                original_file TEXT,
                row_count     INTEGER,
                col_count     INTEGER,
                schema_json   TEXT,
                uploaded_at   TIMESTAMP DEFAULT NOW()
            );
        """)

        print("\n✅ All tables pushed to Neon successfully!")
        print("   Your app is ready to deploy.\n")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    finally:
        local.close()
        neon.close()
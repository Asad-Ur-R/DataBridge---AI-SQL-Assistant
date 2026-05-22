import os
import pandas as pd
from dotenv import load_dotenv
from utils.db import get_connection

load_dotenv()

def load_csv():
    print("Reading CSV")

    df = pd.read_csv("superstore.csv", encoding="latin-1")

    # Lowercase all column names, replace spaces with underscores so it becomes easier to code
    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
        .str.replace(" ", "_")
        .str.replace("-", "_")
    )

    # Convert date columns from string to actual date type so it is stored as date not as text
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["ship_date"]  = pd.to_datetime(df["ship_date"])

    # Convert postal code to string as converted to int will lose 0 in begining
    df["postal_code"] = df["postal_code"].astype(str)

    print(f"{len(df):,} rows loaded")
    return df


# Create Tables

def create_tables(conn):
    print("Creating tables")

    sql = """
        DROP TABLE IF EXISTS query_history CASCADE;
        DROP TABLE IF EXISTS orders       CASCADE;
        DROP TABLE IF EXISTS customers    CASCADE;
        DROP TABLE IF EXISTS products     CASCADE;

        -- CUSTOMERS TABLE
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

        -- PRODUCTS TABLE
        CREATE TABLE products (
            product_id   TEXT PRIMARY KEY,
            product_name TEXT,
            category     TEXT,
            sub_category TEXT
        );

        -- ORDERS TABLE (this is the FACT table)
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

        -- QUERY HISTORY TABLE
        CREATE TABLE query_history (
            id            SERIAL PRIMARY KEY,
            user_question TEXT,
            generated_sql TEXT,
            row_count     INTEGER,
            asked_at      TIMESTAMP DEFAULT NOW()
        );
    """

    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("All tables created")


#  Insert Data

def insert_data(conn, df):

    # Customers
    print("Inserting customers")
    customers = df[[
        "customer_id", "customer_name", "segment",
        "country", "city", "state", "postal_code", "region"
    ]].drop_duplicates(subset="customer_id")

    with conn.cursor() as cur:
        for _, row in customers.iterrows():
            cur.execute("""
                INSERT INTO customers
                    (customer_id, customer_name, segment,
                     country, city, state, postal_code, region)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id) DO NOTHING
            """, tuple(row))
    conn.commit()
    print(f"{len(customers):,} customers inserted")

    # Products
    print("Inserting products...")
    products = df[[
        "product_id", "product_name", "category", "sub_category"
    ]].drop_duplicates(subset="product_id")

    with conn.cursor() as cur:
        for _, row in products.iterrows():
            cur.execute("""
                INSERT INTO products
                    (product_id, product_name, category, sub_category)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
            """, tuple(row))
    conn.commit()
    print(f"{len(products):,} products inserted")

    # Orders
    print("Inserting orders...")
    orders = df[[
        "row_id", "order_id", "order_date", "ship_date", "ship_mode",
        "customer_id", "product_id",
        "sales", "quantity", "discount", "profit"
    ]]

    with conn.cursor() as cur:
        for _, row in orders.iterrows():
            cur.execute("""
                INSERT INTO orders
                    (row_id, order_id, order_date, ship_date, ship_mode,
                     customer_id, product_id,
                     sales, quantity, discount, profit)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (row_id) DO NOTHING
            """, tuple(row))
    conn.commit()
    print(f"{len(orders):,} orders inserted")


# ── STEP D: Verify Everything ─────────────────────────────────
# Why: Always verify after inserting. If a table shows 0 rows
# something silently went wrong and you catch it here immediately.

def verify(conn):
    print("\n Final Row Counts:")
    with conn.cursor() as cur:
        for table in ["customers", "products", "orders", "query_history"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"   {table:<20} → {count:,} rows")


# MAIN
if __name__ == "__main__":
    print("\n Superstore DB Setup")
    print("─" * 40)

    df   = load_csv()
    conn = get_connection()

    try:
        create_tables(conn)
        insert_data(conn, df)
        verify(conn)
        print("\n Database ready! Move to Step 4.\n")
    except Exception as e:
        print(f"\n Error: {e}")
    finally:
        # Open connections use server memory so always close when done.
        conn.close()
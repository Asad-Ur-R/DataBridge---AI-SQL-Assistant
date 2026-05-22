# utils/schema_builder.py
# ─────────────────────────────────────────────────────────────
# Handles the entire CSV → PostgreSQL pipeline.
#
# Given any uploaded CSV file, this module:
#   1. Reads and cleans it
#   2. Detects column types
#   3. Maps them to PostgreSQL types
#   4. Creates the table dynamically
#   5. Inserts all rows
#   6. Stores metadata about the upload
# ─────────────────────────────────────────────────────────────

import re
import sys
import pandas as pd
from pathlib import Path
import io

# Fix import path so utils.db is always findable
sys.path.append(str(Path(__file__).resolve().parent.parent))
from utils.db import get_connection


# ── TYPE MAPPER ───────────────────────────────────────────────
# Why this function is critical:
# PostgreSQL and pandas have completely different type systems.
# If we don't translate correctly, we get errors like:
# "column is type integer but expression is type text"
# This function is the bridge between the two worlds.

def pandas_type_to_postgres(series: pd.Series) -> str:
    """
    Inspects a pandas Series and returns the best PostgreSQL
    data type for that column.

    Why we inspect the SERIES not just the dtype:
    pandas dtype alone isn't always enough. For example,
    an "object" dtype could be plain text, or it could be
    dates stored as strings. We look at actual values to
    decide more accurately.
    """
    dtype = series.dtype

    # ── Integer types ──────────────────────────────────────────
    # Why BIGINT not INTEGER:
    # INTEGER holds up to ~2.1 billion. BIGINT holds up to
    # ~9.2 quintillion. For IDs or large counts, INTEGER can
    # overflow silently. BIGINT is always safe and the
    # performance difference is negligible.
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"

    # ── Float types ───────────────────────────────────────────
    # Why NUMERIC not FLOAT:
    # FLOAT has rounding errors (0.1 + 0.2 = 0.30000000004).
    # NUMERIC stores exact decimal values — critical for money,
    # prices, percentages. Always use NUMERIC for business data.
    if pd.api.types.is_float_dtype(dtype):
        return "NUMERIC"

    # ── Boolean types ─────────────────────────────────────────
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"

    # ── Datetime types ────────────────────────────────────────
    # Why TIMESTAMP not DATE:
    # TIMESTAMP stores both date AND time. If the CSV only has
    # dates (no time component), PostgreSQL still stores it fine
    # as TIMESTAMP — just with 00:00:00 time. Going the other
    # way (DATE when data has times) would LOSE information.
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP"

    # ── Object (string) types — needs deeper inspection ───────
    # Why we can't just use TEXT for everything:
    # We can — TEXT always works. But detecting dates stored as
    # strings and using TIMESTAMP instead makes the data much
    # more queryable. Users can then filter by date ranges,
    # extract years, etc. which is impossible with TEXT dates.
    if pd.api.types.is_object_dtype(dtype):

        # Sample non-null values to check if they look like dates
        # Why sample: checking all rows is slow for large CSVs
        sample = series.dropna().head(50)

        if len(sample) > 0:
            # Try parsing as datetime
            try:
                pd.to_datetime(sample, infer_datetime_format=True)
                # If parsing succeeded → it's a date column
                return "TIMESTAMP"
            except Exception:
                pass

        # Default for all other string data
        return "TEXT"

    # ── Fallback ──────────────────────────────────────────────
    # Why TEXT as fallback:
    # TEXT in PostgreSQL can store literally anything — any
    # length, any content. It's the safest fallback because
    # it never rejects valid data.
    return "TEXT"


# ── COLUMN NAME CLEANER ───────────────────────────────────────
# Why clean column names:
# CSV headers from the wild are messy — spaces, special chars,
# mixed casing, numbers at the start. PostgreSQL column names
# must follow strict rules. We sanitize them here.
#
# Rules:
# - Lowercase everything
# - Replace spaces and special chars with underscores
# - Remove consecutive underscores
# - Can't start with a number → prefix with "col_"

def clean_column_name(name: str) -> str:
    name = str(name).strip().lower()

    # Replace spaces and any non-alphanumeric chars with underscore
    name = re.sub(r"[^a-z0-9]+", "_", name)

    # Remove leading/trailing underscores
    name = name.strip("_")

    # Can't start with a digit — prefix it
    if name and name[0].isdigit():
        name = "col_" + name

    # Replace multiple consecutive underscores with one
    name = re.sub(r"_+", "_", name)

    # Fallback if name becomes empty after cleaning
    if not name:
        name = "column"

    return name


# ── CSV READER & CLEANER
def read_and_clean_csv(file) -> tuple[pd.DataFrame | None, str | None]:
# Why clean before type detection:
# Type detection reads actual values to decide postgres types.
# If "$1,234.56" is still a string when we detect types, it
# gets mapped to TEXT instead of NUMERIC. Cleaning first means
# type detection works on real values, not dirty strings.
    
    def clean_data(df: pd.DataFrame) -> pd.DataFrame:
        """
        Cleans the most common dirty data patterns found in
        real-world CSVs before inserting into PostgreSQL.

        Does NOT modify the original DataFrame — returns a clean copy.
        Why copy: non-destructive — if cleaning fails halfway,
        the original data is still intact.
        """
        df = df.copy()

        # ── 1. Normalize null-like strings → actual NaN ────────────
        # Why: Many tools export missing values as text like "N/A",
        # "na", "none", "-", "null". PostgreSQL doesn't know these
        # mean NULL — it would store them as the literal string "N/A".
        # We replace them with Python None so psycopg2 inserts NULL.
        NULL_PATTERNS = {
            "n/a", "na", "nan", "null", "none",
            "nil", "-", "--", "?", "", " ",
            "not available", "not applicable",
            "#n/a", "#null!", "missing"
        }
        df.replace(
            to_replace=list(NULL_PATTERNS),
            value=None,
            regex=False,
            inplace=True
        )

        # Process each column individually for type-specific cleaning
        for col in df.columns:
            series = df[col]

            # Skip columns that are already fully null
            if series.isna().all():
                continue

            # ── 2. Strip whitespace from string columns ────────────
            # Why: " Alice " and "Alice" are the same person but
            # would be treated as different values in GROUP BY queries.
            # Leading/trailing spaces are invisible and very common
            # in Excel exports.
            if series.dtype == object:
                series = series.str.strip() if hasattr(series, 'str') else series

            # ── 3. Clean currency strings → float ─────────────────
            # Why: Excel often formats numbers as "$1,234.56".
            # These come in as strings and would be stored as TEXT.
            # Removing $ and , converts them to proper numbers.
            if series.dtype == object:
                # Check if this column LOOKS like currency
                # Sample non-null values and test the pattern
                sample = series.dropna().head(20).astype(str)
                currency_pattern = sample.str.match(
                    r"^\s*[$£€¥₹]?\s*[\d,]+\.?\d*\s*$"
                )
                if currency_pattern.sum() >= len(sample) * 0.7:
                    # 70%+ of values look like numbers → clean it
                    try:
                        cleaned = (
                            series.astype(str)
                            .str.replace(r"[$£€¥₹,\s]", "", regex=True)
                            .str.strip()
                        )
                        # Convert to numeric — errors='coerce' turns
                        # unparseable values to NaN instead of crashing
                        numeric = pd.to_numeric(cleaned, errors="coerce")

                        # Only apply if most values converted successfully
                        # Why: If only 20% converted, it's probably not
                        # actually a numeric column — leave it as text
                        if numeric.notna().sum() >= series.notna().sum() * 0.7:
                            series = numeric
                    except Exception:
                        pass

            # ── 4. Clean percentage strings → float ───────────────
            # Why: "45.5%" is a number but stored as string.
            # We strip the % and store as 45.5 (the actual value).
            if series.dtype == object:
                sample = series.dropna().head(20).astype(str)
                pct_pattern = sample.str.match(r"^\s*[\d.]+\s*%\s*$")
                if pct_pattern.sum() >= len(sample) * 0.7:
                    try:
                        cleaned = (
                            series.astype(str)
                            .str.replace("%", "", regex=False)
                            .str.strip()
                        )
                        numeric = pd.to_numeric(cleaned, errors="coerce")
                        if numeric.notna().sum() >= series.notna().sum() * 0.7:
                            series = numeric
                    except Exception:
                        pass

            # ── 5. Clean numeric strings with commas ───────────────
            # Why: "1,234,567" is a number formatted for readability.
            # Python can't parse commas in numbers — we remove them.
            if series.dtype == object:
                sample = series.dropna().head(20).astype(str)
                num_pattern = sample.str.match(r"^\s*[\d,]+\.?\d*\s*$")
                if num_pattern.sum() >= len(sample) * 0.7:
                    try:
                        cleaned = (
                            series.astype(str)
                            .str.replace(",", "", regex=False)
                            .str.strip()
                        )
                        numeric = pd.to_numeric(cleaned, errors="coerce")
                        if numeric.notna().sum() >= series.notna().sum() * 0.7:
                            series = numeric
                    except Exception:
                        pass

            # ── 6. Normalize boolean-like strings → True/False ─────
            # Why: Excel exports booleans as "TRUE"/"FALSE", some
            # systems use "yes"/"no", "1"/"0", "active"/"inactive".
            # Storing as actual booleans makes queries much cleaner:
            # WHERE is_active = TRUE  (not WHERE is_active = 'yes')
            if series.dtype == object:
                sample = series.dropna().head(20).astype(str).str.lower().str.strip()
                bool_true  = {"true", "yes", "1", "y", "active", "on"}
                bool_false = {"false", "no", "0", "n", "inactive", "off"}
                all_bools  = bool_true | bool_false

                if sample.isin(all_bools).sum() >= len(sample) * 0.9:
                    # 90%+ match bool patterns → convert the whole column
                    def to_bool(val):
                        if pd.isna(val):
                            return None
                        v = str(val).lower().strip()
                        if v in bool_true:
                            return True
                        if v in bool_false:
                            return False
                        return None

                    series = series.apply(to_bool)

            # ── 7. Parse date strings → datetime ──────────────────
            # Why: Dates stored as strings like "15-Jan-2023" or
            # "01/15/2023" won't be detected as dates by our type
            # mapper unless we convert them first. Once converted,
            # the type mapper correctly assigns TIMESTAMP.
            if series.dtype == object:
                sample = series.dropna().head(20)
                try:
                    parsed = pd.to_datetime(sample)
                    # If >70% parsed successfully, convert the whole column
                    if parsed.notna().sum() >= len(sample) * 0.7:
                        series = pd.to_datetime(
                            df[col],
                            infer_datetime_format=True,
                            errors="coerce"
                        )
                except Exception:
                    pass

            # Write cleaned series back to DataFrame
            df[col] = series

        # ── 8. Drop fully duplicate rows ───────────────────────────
        # Why: Duplicate rows give wrong aggregation results.
        # "total sales by region" would double-count duplicated rows.
        before = len(df)
        df.drop_duplicates(inplace=True)
        after = len(df)
        if before != after:
            print(f"   🧹 Removed {before - after:,} duplicate rows")

        # ── 9. Reset index after all dropping ──────────────────────
        # Why: After dropping rows, index has gaps (0,1,5,6...).
        # Resetting gives a clean 0,1,2,3... index which prevents
        # subtle bugs during batch insertion.
        df.reset_index(drop=True, inplace=True)

        return df
    """
    Reads a CSV file and performs basic cleaning.

    Why try multiple encodings:
    CSVs from different sources use different character
    encodings. A file from Excel often uses 'latin-1' while
    most modern files use 'utf-8'. Trying both automatically
    prevents cryptic encoding errors for the user.

    Returns:
        (DataFrame, None)   → success
        (None, error_msg)   → failed to read
    """
    # Try UTF-8 first (most common), fall back to latin-1
    for encoding in ["utf-8", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(file, encoding=encoding)

            # Clean column names
            df.columns = [clean_column_name(c) for c in df.columns]

            # Handle duplicate column names after cleaning
            # e.g. "First Name" and "first name" both → "first_name"
            seen = {}
            new_cols = []
            for col in df.columns:
                if col in seen:
                    seen[col] += 1
                    new_cols.append(f"{col}_{seen[col]}")
                else:
                    seen[col] = 0
                    new_cols.append(col)
            df.columns = new_cols

            # Drop completely empty columns and rows
            # Why: Empty columns add noise and can cause type
            # detection to fail (all NaN → unclear type)
            df.dropna(axis=1, how="all", inplace=True)
            df.dropna(axis=0, how="all", inplace=True)

            # Reset index after dropping rows
            df.reset_index(drop=True, inplace=True)

            df = clean_data(df)
            return df, None

        except UnicodeDecodeError:
            # Try next encoding
            continue
        except Exception as e:
            return None, f"Failed to read CSV: {str(e)}"

    return None, "Could not decode file. Try saving your CSV as UTF-8."


# ── TABLE NAME GENERATOR ──────────────────────────────────────
def make_table_name(filename: str) -> str:
    """
    Converts a filename into a valid PostgreSQL table name.

    Why not just use the filename directly:
    Filenames can have spaces, dots, dashes, uppercase —
    all invalid in PostgreSQL table names. We sanitize
    the same way we sanitize column names.

    Examples:
        "Sales Data 2024.csv" → "sales_data_2024"
        "my-report.csv"       → "my_report"
    """
    # Remove file extension
    name = Path(filename).stem

    # Apply same cleaning as columns
    name = clean_column_name(name)

    # Truncate to 50 chars — PostgreSQL allows 63 but we keep headroom
    name = name[:50]

    return name


# ── TABLE CREATOR ─────────────────────────────────────────────
def create_table_from_df(
    df: pd.DataFrame,
    table_name: str,
    conn
) -> tuple[dict | None, str | None]:
    """
    Dynamically creates a PostgreSQL table matching the DataFrame.

    Steps:
    1. Map each pandas column to its PostgreSQL type
    2. Add an auto-increment primary key (_id)
    3. Build the CREATE TABLE SQL string
    4. Execute it

    Why add _id column:
    The CSV might not have a unique identifier column.
    We always add _id SERIAL PRIMARY KEY so every table
    has a reliable way to identify individual rows — this
    is needed for UPDATE and DELETE operations later.

    Returns:
        (schema_dict, None)    → success, schema_dict has type info
        (None, error_msg)      → failed
    """
    # Build column type mapping
    # We store this so the AI prompt can show exact types
    schema = {}
    col_definitions = ["_id SERIAL PRIMARY KEY"]

    for col in df.columns:
        pg_type = pandas_type_to_postgres(df[col])
        schema[col] = pg_type
        col_definitions.append(f'"{col}" {pg_type}')

    # Build full CREATE TABLE statement
    # Why double quotes around column names:
    # Protects against reserved words. If a column is named
    # "order" or "select" (SQL keywords), quoting prevents errors.
    cols_sql = ",\n    ".join(col_definitions)
    create_sql = f"""
        DROP TABLE IF EXISTS "{table_name}" CASCADE;
        CREATE TABLE "{table_name}" (
            {cols_sql}
        );
    """

    try:
        with conn.cursor() as cur:
            cur.execute(create_sql)
        conn.commit()
        return schema, None

    except Exception as e:
        conn.rollback()
        return None, f"Failed to create table: {str(e)}"


# ── DATA INSERTER ─────────────────────────────────────────────
def insert_dataframe(
    df: pd.DataFrame,
    table_name: str,
    schema: dict,
    conn
) -> tuple[int, str | None]:
    """
    Inserts all DataFrame rows into PostgreSQL in one bulk operation.

    Why execute_values instead of executemany:
    executemany sends one INSERT per row (or per batch) as
    separate database round trips. execute_values builds a
    single INSERT with ALL rows as one SQL statement:

    INSERT INTO table (col1, col2) VALUES
        (row1val1, row1val2),
        (row2val1, row2val2),
        ...
        (row10000val1, row10000val2);

    One network round trip instead of thousands.
    For 10,000 rows this is typically 10-20x faster.

    Returns:
        (rows_inserted, None)   → success
        (0, error_msg)          → failed
    """
    # Import here — only this function needs it
    from psycopg2.extras import execute_values

    cols         = list(df.columns)
    col_names    = ", ".join([f'"{c}"' for c in cols])
    insert_sql   = f'INSERT INTO "{table_name}" ({col_names}) VALUES %s'

    # Convert entire DataFrame to list of tuples in one shot
    # Why where() trick: replaces NaN with None (PostgreSQL NULL)
    # Why list of tuples: execute_values expects exactly this format
    df_clean = df.where(pd.notnull(df), None)
    rows     = [tuple(row) for row in df_clean.itertuples(index=False, name=None)]

    # Why itertuples not iterrows:
    # itertuples is ~10x faster than iterrows because it
    # returns named tuples (C-level) instead of Series objects.
    # For 10,000 rows the difference is seconds vs milliseconds.

    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                insert_sql,
                rows,
                # page_size controls how many rows per SQL statement
                # Why 1000: sweet spot — large enough for speed,
                # small enough to not exceed PostgreSQL's parameter limit
                page_size=1000
            )
        conn.commit()
        return len(rows), None

    except Exception as e:
        conn.rollback()
        return 0, f"Failed to insert data: {str(e)}"


# ── METADATA STORE ────────────────────────────────────────────
# Why store metadata:
# When the user comes back or refreshes the app, we need to
# remember what table they uploaded, what columns it has, and
# what types those columns are. We store this in a system
# table called _uploaded_tables so the app can restore state.

def ensure_metadata_table(conn):
    """
    Creates the _uploaded_tables metadata table if it doesn't exist.
    This runs every time the app starts — safe because of IF NOT EXISTS.
    """
    sql = """
        CREATE TABLE IF NOT EXISTS _uploaded_tables (
            id           SERIAL PRIMARY KEY,
            table_name   TEXT NOT NULL,
            original_file TEXT,
            row_count    INTEGER,
            col_count    INTEGER,
            schema_json  TEXT,
            uploaded_at  TIMESTAMP DEFAULT NOW()
        );
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def save_metadata(
    conn,
    table_name: str,
    filename: str,
    row_count: int,
    schema: dict
):
    """Saves info about the uploaded table to _uploaded_tables."""
    import json

    # Remove old entry for same table name if re-uploading
    with conn.cursor() as cur:
        cur.execute(
            'DELETE FROM _uploaded_tables WHERE table_name = %s',
            (table_name,)
        )
        cur.execute("""
            INSERT INTO _uploaded_tables
                (table_name, original_file, row_count, col_count, schema_json)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            table_name,
            filename,
            row_count,
            len(schema),
            json.dumps(schema)
        ))
    conn.commit()


def get_uploaded_tables(conn) -> list[dict]:
    """
    Returns list of all previously uploaded tables.
    Used to let user switch between datasets they've uploaded.
    """
    import json
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name, original_file, row_count,
                       col_count, schema_json, uploaded_at
                FROM _uploaded_tables
                ORDER BY uploaded_at DESC
            """)
            rows = cur.fetchall()

        return [
            {
                "table_name":    r[0],
                "original_file": r[1],
                "row_count":     r[2],
                "col_count":     r[3],
                "schema":        json.loads(r[4]),
                "uploaded_at":   r[5],
            }
            for r in rows
        ]
    except Exception:
        return []



# ── MASTER FUNCTION ───────────────────────────────────────────
# This is the ONLY function app.py calls for uploads.
# It runs every step in the correct order and returns a
# single clean result — success or a clear error message.
#
# Why one master function:
# app.py shouldn't know or care about the internal steps.
# It just calls process_csv_upload() and gets back either
# a result dict or an error string. This is called the
# "facade pattern" — hide complexity behind a simple interface.

def process_csv_upload(
    file,
    filename: str
) -> tuple[dict | None, str | None]:
    """
    Full pipeline: uploaded file → cleaned → PostgreSQL table.

    Steps:
        1. Read CSV (tries multiple encodings)
        2. Clean data (nulls, currency, booleans, dates, duplicates)
        3. Generate safe table name from filename
        4. Connect to PostgreSQL
        5. Create table dynamically from column types
        6. Bulk insert all rows using execute_values (fast)
        7. Save metadata to _uploaded_tables registry
        8. Return result summary

    Why bulk insert with execute_values:
    Old approach used executemany which sends one INSERT per
    batch as separate database round trips. execute_values
    builds a SINGLE INSERT statement with ALL rows:

        INSERT INTO table (col1, col2) VALUES
            (val1, val2),
            (val3, val4),
            ...all 10,000 rows in one shot

    One network round trip instead of thousands.
    10,000 rows: old way ~2-3 min → new way ~3-5 seconds.

    Args:
        file:     File-like object (from Streamlit uploader
                  or io.BytesIO / io.StringIO for testing)
        filename: Original filename — used to generate table name

    Returns:
        (result_dict, None)   → success
        (None, error_msg)     → something failed

    result_dict contains:
        table_name  → name of the created PostgreSQL table
        row_count   → how many rows were inserted
        col_count   → how many columns
        schema      → {column_name: postgres_type} mapping
        df_preview  → first 5 rows as DataFrame for display
    """
    from psycopg2.extras import execute_values

    # ── Step 1: Read & clean CSV ──────────────────────────────
    # Why clean before anything else:
    # Type detection reads actual values to pick postgres types.
    # If "$1,234.56" is still a string during type detection,
    # that column becomes TEXT instead of NUMERIC.
    # Cleaning first means type detection works on real values.
    print(f"\n📂 Reading: {filename}")
    df, error = read_and_clean_csv(file)

    if error:
        return None, f"Could not read file: {error}"

    if df is None or df.empty:
        return None, "The uploaded file appears to be empty."

    if len(df.columns) == 0:
        return None, "No valid columns found in the file."

    print(f"   ✅ {len(df):,} rows × {len(df.columns)} columns loaded")

    # ── Step 2: Generate table name ───────────────────────────
    # Why from filename:
    # The table name should reflect what the data is about.
    # "sales_2024.csv" → table "sales_2024" is intuitive.
    # make_table_name() sanitizes it to be PostgreSQL-safe.
    table_name = make_table_name(filename)
    print(f"   📋 Table name: {table_name}")

    # ── Step 3: Connect to PostgreSQL ─────────────────────────
    # Why connect once and reuse:
    # Opening a connection is expensive (~100ms).
    # We open one connection and pass it to every step
    # so all operations happen in the same session.
    # The finally block guarantees it always closes.
    conn = None
    try:
        conn = get_connection()
        print(f"   🔌 Connected to database")

        # ── Step 4: Detect column types ───────────────────────
        # Why build schema before CREATE TABLE:
        # We need the postgres type for each column BEFORE
        # we can write the CREATE TABLE SQL. We build the
        # full mapping here then use it in step 5.
        print(f"   🔍 Detecting column types...")
        schema      = {}
        col_defs    = ["_id SERIAL PRIMARY KEY"]

        for col in df.columns:
            pg_type         = pandas_type_to_postgres(df[col])
            schema[col]     = pg_type
            col_defs.append(f'"{col}" {pg_type}')
            print(f"      {col:<30} → {pg_type}")

        # ── Step 5: Create table ──────────────────────────────
        # Why DROP IF EXISTS first:
        # If the user re-uploads the same file, we recreate
        # the table fresh instead of erroring on a duplicate.
        # CASCADE drops any dependent objects (indexes, etc.)
        print(f"\n   🛠️  Creating table '{table_name}'...")

        cols_sql   = ",\n        ".join(col_defs)
        create_sql = f"""
            DROP TABLE IF EXISTS "{table_name}" CASCADE;
            CREATE TABLE "{table_name}" (
                {cols_sql}
            );
        """

        with conn.cursor() as cur:
            cur.execute(create_sql)
        conn.commit()
        print(f"   ✅ Table created with {len(schema)} columns")

        # ── Step 6: Bulk insert ───────────────────────────────
        # Why execute_values:
        # Sends ALL rows in one SQL statement — one network
        # round trip instead of thousands. Dramatically faster
        # for any dataset larger than a few hundred rows.
        #
        # Why itertuples not iterrows:
        # itertuples returns C-level named tuples — ~10x faster
        # than iterrows which wraps each row in a pandas Series.
        # For 10,000 rows this difference is seconds vs minutes.
        #
        # Why where(notnull, None):
        # pandas uses NaN for missing values. PostgreSQL uses NULL.
        # psycopg2 doesn't auto-convert NaN → NULL so we do it
        # manually. where() replaces every NaN with Python None
        # which psycopg2 correctly inserts as NULL.
        print(f"\n   📥 Inserting {len(df):,} rows...")

        col_names  = ", ".join([f'"{c}"' for c in df.columns])
        insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES %s'

        df_clean   = df.where(pd.notnull(df), None)
        rows       = [
            tuple(row)
            for row in df_clean.itertuples(index=False, name=None)
        ]

        with conn.cursor() as cur:
            execute_values(
                cur,
                insert_sql,
                rows,
                # page_size = rows per SQL statement
                # 1000 is sweet spot: fast but never hits
                # PostgreSQL's parameter limit (65535 params max)
                page_size=1000
            )
        conn.commit()

        rows_inserted = len(rows)
        print(f"   ✅ {rows_inserted:,} rows inserted successfully")

        # ── Step 7: Save metadata ─────────────────────────────
        # Why store metadata:
        # When the app restarts or user refreshes, we need to
        # remember what tables exist, their schemas, and file
        # names. _uploaded_tables is our registry for this.
        # Without it every refresh would show an empty sidebar.
        print(f"   💾 Saving metadata...")
        ensure_metadata_table(conn)
        save_metadata(
            conn       = conn,
            table_name = table_name,
            filename   = filename,
            row_count  = rows_inserted,
            schema     = schema
        )
        print(f"   ✅ Metadata saved")

        # ── Step 8: Return result ─────────────────────────────
        print(f"\n✅ Upload complete — '{table_name}' is ready\n")
        return {
            "table_name":  table_name,
            "row_count":   rows_inserted,
            "col_count":   len(schema),
            "schema":      schema,
            "df_preview":  df.head(5),
        }, None

    except Exception as e:
        # Rollback any partial changes if something failed mid-way
        # Why rollback: Without it a half-created table or
        # partial insert could corrupt the database state.
        if conn:
            try:
                conn.rollback()
                print(f"   ↩️  Rolled back partial changes")
            except Exception:
                pass
        return None, f"Upload failed: {str(e)}"

    finally:
        # Always close — even if an exception occurred above.
        # Why finally: if we returned early due to an error,
        # the connection would stay open forever without this.
        if conn:
            conn.close()
            print(f"   🔌 Connection closed")


if __name__ == "__main__":
    import io

    print("\n🧪 Testing Schema Builder + Data Cleaning")
    print("─" * 40)

    # Dirty CSV with real-world messy patterns
    test_csv = """name,age,salary,join_date,is_active,score
Alice,28,"$55,000.50",2021-03-15,yes,45.5%
Bob,34,"$72,000.00",2019-08-22,no,62.0%
Charlie,N/A,"$48,000.75",2023-01-10,TRUE,78.3%
Diana,31,"$91,000.00",2020-11-05,active,91.1%
  Eve  ,25,"$48,000.75",2023-01-10,TRUE,78.3%
Charlie,N/A,"$48,000.75",2023-01-10,TRUE,78.3%
n/a,n/a,n/a,n/a,n/a,n/a
"""
    print("\n  Testing data cleaning on dirty CSV:")
    file = io.StringIO(test_csv)
    df, err = read_and_clean_csv(file)

    if err:
        print(f"   ❌ {err}")
    else:
        print(f"\n   Cleaned DataFrame ({len(df)} rows):")
        print(df.to_string(index=False))
        print(f"\n   Column types after cleaning:")
        for col in df.columns:
            pg_type = pandas_type_to_postgres(df[col])
            print(f"   {col:<15} → {str(df[col].dtype):<15} → {pg_type}")

    print("\n  Testing full upload pipeline with dirty data:")
    file = io.StringIO(test_csv)
    result, error = process_csv_upload(file, "dirty_test.csv")

    if error:
        print(f"   ❌ Error: {error}")
    else:
        print(f"   ✅ Table   : {result['table_name']}")
        print(f"   ✅ Rows    : {result['row_count']}")
        print(f"   ✅ Schema  : {result['schema']}")

    print("\n✅ Done! Ready to move on.\n")
# utils/security.py
# ─────────────────────────────────────────────────────────────
# Security layer — validates ALL SQL before execution.
#
# This file is the single checkpoint every query passes
# through before touching the database. No exceptions.
#
# Why a dedicated security file:
# Mixing security checks into db.py or app.py means they
# can accidentally get skipped. A dedicated file makes
# security explicit — you MUST call validate() or the
# query never runs.
# ─────────────────────────────────────────────────────────────

import re


# ── BLOCKED PATTERNS ──────────────────────────────────────────
# Why these specific patterns:
#
# STRUCTURAL_DANGER: Commands that modify the database STRUCTURE
# (not just data). These are irreversible and catastrophic.
# DROP TABLE deletes the entire table permanently.
# TRUNCATE wipes all rows with no recovery.
# ALTER TABLE changes column definitions.
# These should NEVER come from user input under any circumstances.
#
# INJECTION_PATTERNS: Classic SQL injection techniques.
# Stacked queries (;SELECT) let attackers chain extra commands.
# Comment sequences (--) are used to bypass WHERE clauses.
# UNION SELECT is used to extract data from other tables.
#
# SYSTEM_ACCESS: PostgreSQL-specific commands that access
# server internals, file system, or other databases.

STRUCTURAL_DANGER = [
    r"\bDROP\s+TABLE\b",
    r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+SCHEMA\b",
    r"\bTRUNCATE\b",
    r"\bALTER\s+TABLE\b",
    r"\bALTER\s+DATABASE\b",
    r"\bCREATE\s+TABLE\b",
    r"\bCREATE\s+DATABASE\b",
    r"\bDROP\s+INDEX\b",
]

INJECTION_PATTERNS = [
    r";\s*SELECT",          # Stacked queries
    r";\s*DROP",            # Stacked drop
    r";\s*DELETE",          # Stacked delete
    r";\s*INSERT",          # Stacked insert
    r";\s*UPDATE",          # Stacked update
    r"--\s",                # SQL comment (injection technique)
    r"/\*.*?\*/",           # Block comments
    r"\bUNION\s+SELECT\b",  # UNION injection
    r"\bOR\s+1\s*=\s*1\b",  # Classic OR injection
    r"\bOR\s+'1'\s*=\s*'1'",# String OR injection
]

SYSTEM_ACCESS = [
    r"\bpg_catalog\b",      # PostgreSQL system catalog
    r"\binformation_schema\b", # DB metadata tables
    r"\bpg_shadow\b",       # Password hashes
    r"\bpg_user\b",         # User accounts
    r"\bCOPY\b",            # File system access
    r"\\\w+",               # psql meta-commands
    r"\bEXECUTE\b",         # Dynamic SQL execution
    r"\bEVAL\b",            # Dynamic evaluation
]


def validate_sql(sql: str, operation_type: str = "query") -> tuple[bool, str | None]:
    """
    Validates a SQL string before execution.

    Args:
        sql:            The SQL string to validate
        operation_type: "query" (SELECT only)
                        "crud"  (INSERT/UPDATE/DELETE allowed)

    Returns:
        (True, None)         → SQL is safe to execute
        (False, reason_msg)  → SQL blocked, reason explains why

    Why check operation_type:
    SELECT and CRUD have different allowed patterns.
    A SELECT validator would block INSERT which is valid in CRUD mode.
    We validate differently based on what the user is trying to do.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL — nothing to execute."

    sql_upper = sql.upper().strip()

    # ── 1. Check structural danger patterns ───────────────────
    # These are blocked regardless of operation type
    for pattern in STRUCTURAL_DANGER:
        if re.search(pattern, sql_upper):
            matched = re.search(pattern, sql_upper).group()
            return False, (
                f"🚫 Blocked: '{matched}' is not allowed. "
                f"Structural database changes are disabled for safety."
            )

    # ── 2. Check injection patterns ───────────────────────────
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, sql_upper, re.DOTALL):
            return False, (
                f"🚫 Blocked: Potentially dangerous SQL pattern detected. "
                f"Please rephrase your request."
            )

    # ── 3. Check system table access ──────────────────────────
    for pattern in SYSTEM_ACCESS:
        if re.search(pattern, sql_upper):
            return False, (
                f"🚫 Blocked: Access to system tables or "
                f"server internals is not allowed."
            )

    # ── 4. Operation-specific checks ──────────────────────────

    if operation_type == "query":
        # In query mode ONLY SELECT is allowed
        # Why strip comments before checking:
        # Someone could write "/*DELETE*/SELECT" to trick a simple
        # startswith() check. We strip comments first.
        sql_no_comments = re.sub(r"/\*.*?\*/", "", sql_upper, flags=re.DOTALL)
        sql_no_comments = re.sub(r"--.*$", "", sql_no_comments, flags=re.MULTILINE)
        first_word = sql_no_comments.strip().split()[0] if sql_no_comments.strip() else ""

        if first_word != "SELECT":
            return False, (
                f"🚫 Blocked: Only SELECT queries are allowed in query mode. "
                f"Got '{first_word}' instead."
            )

    elif operation_type == "crud":
        # In CRUD mode only INSERT, UPDATE, DELETE are allowed
        allowed_starts = {"INSERT", "UPDATE", "DELETE"}
        first_word = sql_upper.strip().split()[0] if sql_upper.strip() else ""

        if first_word not in allowed_starts:
            return False, (
                f"🚫 Blocked: Only INSERT, UPDATE, DELETE are allowed "
                f"in modification mode. Got '{first_word}'."
            )

        # ── 5. Require WHERE clause for UPDATE and DELETE ──────
        # Why this is critical:
        # DELETE FROM table (no WHERE) = wipe entire table.
        # UPDATE table SET x=1 (no WHERE) = change every single row.
        # Both are almost always mistakes, never intentional.
        # This single check prevents the most catastrophic user errors.
        if first_word in {"DELETE", "UPDATE"}:
            if "WHERE" not in sql_upper:
                return False, (
                    f"🚫 Blocked: {first_word} without a WHERE clause would "
                    f"affect ALL rows in your table. Please add a condition.\n"
                    f"Example: Add 'WHERE column_name = value' to your request."
                )

    # ── 6. Length check ───────────────────────────────────────
    # Why: Extremely long SQL (>5000 chars) is suspicious —
    # legitimate queries are never this long. Could indicate
    # someone trying to abuse the system.
    if len(sql) > 5000:
        return False, "🚫 Blocked: SQL query is unusually long. Please simplify your request."

    # All checks passed
    return True, None


def validate_file_upload(filename: str, file_size_bytes: int) -> tuple[bool, str | None]:
    """
    Validates a file before processing the upload.

    Why validate uploads:
    Without validation, a user could upload:
    - A 500MB file that crashes the server
    - A .exe or .py file disguised as a CSV
    - A file with a valid extension but malicious content

    Returns:
        (True, None)        → file is safe to process
        (False, reason_msg) → file rejected
    """
    # Check file extension
    allowed_extensions = {".csv", ".tsv", ".txt"}
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in allowed_extensions:
        return False, (
            f"❌ File type '{ext}' is not supported. "
            f"Please upload a CSV file (.csv)"
        )

    # Check file size (max 50MB)
    # Why 50MB: Large enough for real business data (100K+ rows)
    # but small enough to prevent server overload
    max_size = 50 * 1024 * 1024  # 50MB in bytes
    if file_size_bytes > max_size:
        size_mb = file_size_bytes / (1024 * 1024)
        return False, (
            f"❌ File too large ({size_mb:.1f}MB). "
            f"Maximum allowed size is 50MB."
        )

    # Check minimum size (empty file)
    if file_size_bytes < 10:
        return False, "❌ File appears to be empty."

    return True, None


def sanitize_table_name(name: str) -> str:
    """
    Ensures a table name is safe to use in SQL.

    Why needed:
    Table names come from filenames which users control.
    A filename like "'; DROP TABLE users; --" would be
    catastrophic if used directly in SQL without sanitizing.
    We enforce strict alphanumeric + underscore only.
    """
    # Keep only alphanumeric and underscores
    clean = re.sub(r"[^a-z0-9_]", "_", name.lower())

    # Must start with letter or underscore
    if clean and clean[0].isdigit():
        clean = "t_" + clean

    # Truncate to safe length
    clean = clean[:50]

    # Fallback
    if not clean:
        clean = "uploaded_table"

    return clean


# ── TEST ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔒 Testing Security Layer")
    print("─" * 40)

    tests = [
        # (sql, operation_type, should_pass, description)
        (
            'SELECT * FROM "sales" LIMIT 10',
            "query", True,
            "Normal SELECT"
        ),
        (
            'DROP TABLE "sales"',
            "query", False,
            "DROP TABLE attempt"
        ),
        (
            "SELECT * FROM sales; DROP TABLE sales",
            "query", False,
            "Stacked query injection"
        ),
        (
            "SELECT * FROM pg_shadow",
            "query", False,
            "System table access"
        ),
        (
            'DELETE FROM "sales" WHERE "profit" < 0',
            "crud", True,
            "Valid DELETE with WHERE"
        ),
        (
            'DELETE FROM "sales"',
            "crud", False,
            "DELETE without WHERE (blocks full wipe)"
        ),
        (
            'UPDATE "sales" SET "quantity" = 5',
            "crud", False,
            "UPDATE without WHERE"
        ),
        (
            'UPDATE "sales" SET "quantity" = 5 WHERE "_id" = 1',
            "crud", True,
            "Valid UPDATE with WHERE"
        ),
        (
            "SELECT * FROM sales WHERE id=1 OR 1=1",
            "query", False,
            "OR injection attempt"
        ),
        (
            'INSERT INTO "sales" ("product", "sales") VALUES (\'Chair\', 500)',
            "crud", True,
            "Valid INSERT"
        ),
    ]

    passed = 0
    failed = 0

    for sql, op_type, should_pass, description in tests:
        is_safe, reason = validate_sql(sql, op_type)
        result_ok = (is_safe == should_pass)

        status = "✅" if result_ok else "❌ UNEXPECTED"
        outcome = "ALLOWED" if is_safe else "BLOCKED"

        print(f"\n{status} {description}")
        print(f"   Expected: {'PASS' if should_pass else 'BLOCK'} | Got: {outcome}")
        if reason:
            print(f"   Reason: {reason}")

        if result_ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{'─'*40}")
    print(f"Results: {passed} passed, {failed} failed")
    print("✅ Security layer ready!\n" if failed == 0 else "❌ Fix failing tests before proceeding\n")
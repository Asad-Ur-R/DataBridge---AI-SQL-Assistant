# utils/config.py
# ─────────────────────────────────────────────────────────────
# Unified config loader — works in both environments:
#
#   Local development  → reads from .env file
#   Streamlit Cloud    → reads from st.secrets
#
# Why a dedicated config file:
# Without this, every file (db.py, ai.py) would need its own
# logic to handle both environments. One config file means
# one place to change if the loading logic ever needs updating.
# This is called the "single source of truth" principle.
#
# How it works:
# 1. Try to import streamlit and read st.secrets
# 2. If that fails (running locally without streamlit) or
#    the key isn't in secrets → fall back to os.getenv()
# 3. If neither has it → return the default value
# ─────────────────────────────────────────────────────────────

import os
from dotenv import load_dotenv

# Load .env for local development
# Why load here: This runs once when config.py is first imported.
# All subsequent get() calls can use os.getenv() as fallback.
load_dotenv()


def get(key: str, default: str = "") -> str:
    """
    Retrieves a config value from Streamlit secrets or .env.

    Priority order:
        1. st.secrets        (Streamlit Cloud deployment)
        2. os.getenv / .env  (local development)
        3. default           (fallback if neither has it)

    Args:
        key:     The config key e.g. "DB_PASSWORD"
        default: Value to return if key not found anywhere

    Returns:
        The config value as a string
    """
    # Try Streamlit secrets first
    # Why try/except instead of checking if streamlit is installed:
    # Even if streamlit is installed, st.secrets throws an error
    # when running outside a Streamlit context (e.g. python utils/db.py).
    # try/except handles both cases cleanly.
    try:
        import streamlit as st
        val = st.secrets.get(key)
        if val is not None:
            return str(val)
    except Exception:
        # Not running in Streamlit context — that's fine
        pass

    # Fall back to environment variable / .env file
    val = os.getenv(key, default)
    return str(val) if val is not None else default


def get_db_config() -> dict:
    """
    Returns database connection config.

    Why check for NEON_HOST:
    When deployed on Streamlit Cloud, NEON_HOST will be set
    in secrets but DB_HOST won't (or will still be localhost).
    Checking NEON_HOST lets us automatically use the right
    database without changing any other code.

    Priority:
        1. Neon (if NEON_HOST is set → we're deployed)
        2. Local PostgreSQL (fallback for development)
    """
    neon_host = get("NEON_HOST", "")

    if neon_host.strip():
        # Running on Streamlit Cloud → use Neon
        return {
            "host":     neon_host,
            "port":     get("NEON_PORT",     "5432"),
            "dbname":   get("NEON_NAME",     "neondb"),
            "user":     get("NEON_USER",     ""),
            "password": get("NEON_PASSWORD", ""),
            "sslmode":  "require",   # Neon requires SSL — always
        }
    else:
        # Running locally → use local PostgreSQL
        return {
            "host":     get("DB_HOST",     "localhost"),
            "port":     get("DB_PORT",     "5432"),
            "dbname":   get("DB_NAME",     "superstore_db"),
            "user":     get("DB_USER",     "postgres"),
            "password": get("DB_PASSWORD", ""),
        }


def get_groq_keys() -> list[str]:
    """
    Returns all available Groq API keys as a list.
    Filters out any empty/unfilled keys automatically.
    """
    keys = [
        get("GROQ_API_KEY_1"),
        get("GROQ_API_KEY_2"),
    ]
    valid = [k for k in keys if k.strip()]

    if not valid:
        raise ValueError(
            "No Groq API keys found. "
            "Add them to .env (local) or Streamlit Secrets (deployed)."
        )

    return valid
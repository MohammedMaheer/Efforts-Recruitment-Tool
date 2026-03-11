"""
PostgreSQL Compatibility Layer
==============================
Provides transparent SQLite ↔ PostgreSQL compatibility so the same
application code works with both backends.

Usage:
    DATABASE_URL=./recruitment.db          → SQLite (local dev)
    DATABASE_URL=postgresql://user:pass@host/db  → PostgreSQL (production)

Cloud Run connects to Cloud SQL via Unix socket:
    DATABASE_URL=postgresql://user:pass@/db?host=/cloudsql/PROJECT:REGION:INSTANCE
"""
import re
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Detect backend
_database_url = os.getenv("DATABASE_URL", "./recruitment.db")
IS_POSTGRES = _database_url.startswith("postgres")


def get_database_url() -> str:
    """Return the configured DATABASE_URL"""
    return _database_url


def convert_placeholder(sql: str) -> str:
    """Convert SQLite '?' placeholders to PostgreSQL '$1, $2, ...' format.
    
    Only converts '?' that are actual parameter placeholders —
    skips '?' inside string literals.
    """
    if not IS_POSTGRES:
        return sql
    
    result = []
    param_idx = 0
    in_string = False
    i = 0
    
    while i < len(sql):
        ch = sql[i]
        
        if ch == "'":
            in_string = not in_string
            result.append(ch)
        elif ch == '?' and not in_string:
            param_idx += 1
            result.append(f'${param_idx}')
        else:
            result.append(ch)
        
        i += 1
    
    return ''.join(result)


def convert_query(sql: str) -> str:
    """Convert a SQLite SQL query to PostgreSQL-compatible syntax.
    
    Handles:
    - INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE
    - INSERT OR IGNORE  → INSERT ... ON CONFLICT DO NOTHING
    - datetime('now')   → NOW()
    - datetime('now', '-N days') → NOW() - INTERVAL 'N days'
    - date(column)      → column::date
    - ? placeholders    → $1, $2, ...
    - PRAGMA statements → empty string (skipped)
    - AUTOINCREMENT     → (removed, SERIAL handles it)
    - INTEGER for bools → kept as-is (PG handles 0/1 in INTEGER columns)
    """
    if not IS_POSTGRES:
        return sql
    
    # Skip PRAGMA statements entirely
    if sql.strip().upper().startswith('PRAGMA'):
        return ''
    
    # datetime('now') → NOW()
    sql = re.sub(r"datetime\s*\(\s*'now'\s*\)", "NOW()", sql, flags=re.IGNORECASE)
    
    # datetime('now', '-N days') → NOW() - INTERVAL 'N days'
    sql = re.sub(
        r"datetime\s*\(\s*'now'\s*,\s*'(-?\d+)\s*(day|hour|minute|second)s?'\s*\)",
        r"NOW() + INTERVAL '\1 \2s'",
        sql,
        flags=re.IGNORECASE
    )
    
    # date(column) → (column)::date
    sql = re.sub(r"\bdate\((\w+)\)", r"(\1)::date", sql, flags=re.IGNORECASE)
    
    # AUTOINCREMENT → removed (PostgreSQL uses SERIAL)
    sql = re.sub(r'\bAUTOINCREMENT\b', '', sql, flags=re.IGNORECASE)
    
    # Convert ? placeholders to $N
    sql = convert_placeholder(sql)
    
    return sql


def convert_insert_or_replace(sql: str, conflict_columns: Optional[str] = None) -> str:
    """Convert INSERT OR REPLACE to PostgreSQL ON CONFLICT DO UPDATE.
    
    Args:
        sql: The INSERT OR REPLACE SQL statement
        conflict_columns: Comma-separated column names for ON CONFLICT clause.
                         If None, tries to auto-detect from PRIMARY KEY hints.
    """
    if not IS_POSTGRES:
        return sql
    
    # Match: INSERT OR REPLACE INTO table (cols) VALUES (...)
    match = re.match(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        sql,
        re.IGNORECASE
    )
    
    if not match:
        # If we can't parse it, fall through and hope for the best
        return convert_query(sql)
    
    table = match.group(1)
    columns_str = match.group(2)
    values_str = match.group(3)
    
    columns = [c.strip() for c in columns_str.split(',')]
    
    # Auto-detect conflict column
    if not conflict_columns:
        # Heuristic: first column is usually the PK
        conflict_columns = columns[0]
    
    # Build ON CONFLICT DO UPDATE SET for all non-PK columns
    conflict_cols_list = [c.strip() for c in conflict_columns.split(',')]
    update_cols = [c for c in columns if c not in conflict_cols_list]
    
    if update_cols:
        update_set = ', '.join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        pg_sql = f"INSERT INTO {table} ({columns_str}) VALUES ({values_str}) ON CONFLICT ({conflict_columns}) DO UPDATE SET {update_set}"
    else:
        pg_sql = f"INSERT INTO {table} ({columns_str}) VALUES ({values_str}) ON CONFLICT ({conflict_columns}) DO NOTHING"
    
    return convert_query(pg_sql)


def convert_insert_or_ignore(sql: str, conflict_columns: Optional[str] = None) -> str:
    """Convert INSERT OR IGNORE to PostgreSQL ON CONFLICT DO NOTHING."""
    if not IS_POSTGRES:
        return sql
    
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
    
    # Add ON CONFLICT DO NOTHING if not already present
    if "ON CONFLICT" not in sql.upper():
        sql = sql.rstrip().rstrip(';') + " ON CONFLICT DO NOTHING"
    
    return convert_query(sql)


def sqlite_to_pg_ddl(sql: str) -> str:
    """Convert SQLite CREATE TABLE DDL to PostgreSQL-compatible DDL.
    
    Handles type mappings and syntax differences.
    """
    if not IS_POSTGRES:
        return sql
    
    # AUTOINCREMENT → removed (use SERIAL or GENERATED ALWAYS AS IDENTITY)
    sql = re.sub(r'\bAUTOINCREMENT\b', '', sql, flags=re.IGNORECASE)
    
    # BLOB → BYTEA
    sql = re.sub(r'\bBLOB\b', 'BYTEA', sql, flags=re.IGNORECASE)
    
    # Keep INTEGER for boolean columns (PG handles 0/1 fine in INTEGER)
    # Keep TEXT for dates (PG can handle text dates; safer than schema change)
    
    # datetime('now') → NOW() in defaults won't work in DDL; use CURRENT_TIMESTAMP
    sql = re.sub(r"datetime\s*\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
    
    return sql


def convert_alter_table(sql: str) -> str:
    """Convert ALTER TABLE for PostgreSQL compatibility.
    
    PostgreSQL doesn't support ADD COLUMN IF NOT EXISTS before v9.6
    but Cloud SQL Postgres 15 does, so we just clean up the syntax.
    """
    if not IS_POSTGRES:
        return sql
    
    # SQLite: ALTER TABLE t ADD COLUMN c TYPE DEFAULT val
    # This same syntax works in PostgreSQL.
    return sql


# DDL for creating tables in PostgreSQL
PG_SCHEMA_CANDIDATES = """
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    email_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    location TEXT,
    skills TEXT,
    experience INTEGER,
    education TEXT,
    summary TEXT,
    work_history TEXT,
    linkedin TEXT,
    status TEXT DEFAULT 'New',
    match_score REAL DEFAULT 0.0,
    job_category TEXT,
    job_subcategory TEXT,
    applied_date TEXT,
    last_updated TEXT,
    raw_email_subject TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ai_analysis TEXT,
    certifications TEXT,
    languages TEXT,
    resume_text TEXT,
    strengths TEXT,
    gaps TEXT
)
"""

PG_SCHEMA_RESUMES = """
CREATE TABLE IF NOT EXISTS resumes (
    candidate_id TEXT PRIMARY KEY,
    filename TEXT,
    content_type TEXT,
    file_data BYTEA,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
)
"""

PG_SCHEMA_AI_SCORE_CACHE = """
CREATE TABLE IF NOT EXISTS ai_score_cache (
    candidate_id TEXT,
    job_id TEXT,
    ai_score INTEGER,
    strengths TEXT,
    gaps TEXT,
    recommendation TEXT,
    cached_at TEXT,
    PRIMARY KEY (candidate_id, job_id)
)
"""

PG_SCHEMA_EMAIL_LOG = """
CREATE TABLE IF NOT EXISTS email_processing_log (
    message_id TEXT PRIMARY KEY,
    processed_at TEXT,
    candidate_id TEXT,
    action TEXT,
    processing_time_ms INTEGER
)
"""

PG_SCHEMA_SYNC_METADATA = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
)
"""

PG_SCHEMA_JOB_DESCRIPTIONS = """
CREATE TABLE IF NOT EXISTS job_descriptions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT,
    description TEXT,
    required_skills TEXT,
    experience_required TEXT,
    auto_generated INTEGER DEFAULT 1,
    candidate_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)
"""

PG_SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    role TEXT DEFAULT 'Recruiter',
    company TEXT,
    phone TEXT,
    avatar_url TEXT,
    is_active INTEGER DEFAULT 1,
    email_verified INTEGER DEFAULT 0,
    last_login TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

PG_SCHEMA_SEARCH_HISTORY = """
CREATE TABLE IF NOT EXISTS search_history (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    description TEXT,
    result_count INTEGER DEFAULT 0,
    top_results TEXT,
    searched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT
)
"""

# Indexes (same syntax works for both SQLite and PostgreSQL)
PG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_candidates_email ON candidates(email)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_email_hash ON candidates(email_hash)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_match_score ON candidates(match_score)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_job_category ON candidates(job_category)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_last_updated ON candidates(last_updated)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_created_at ON candidates(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_email_log_processed_at ON email_processing_log(processed_at)",
    "CREATE INDEX IF NOT EXISTS idx_email_log_candidate ON email_processing_log(candidate_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_descriptions_category ON job_descriptions(category)",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
    "CREATE INDEX IF NOT EXISTS idx_search_date ON search_history(searched_at DESC)",
]

ALL_PG_SCHEMAS = [
    PG_SCHEMA_CANDIDATES,
    PG_SCHEMA_RESUMES,
    PG_SCHEMA_AI_SCORE_CACHE,
    PG_SCHEMA_EMAIL_LOG,
    PG_SCHEMA_SYNC_METADATA,
    PG_SCHEMA_JOB_DESCRIPTIONS,
    PG_SCHEMA_USERS,
    PG_SCHEMA_SEARCH_HISTORY,
]


def _safe_url_for_log(url: str) -> str:
    """Redact credentials from database URL before logging."""
    if '@' in url and '://' in url:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            safe = f"{parsed.scheme}://***@{parsed.hostname}"
            if parsed.port:
                safe += f":{parsed.port}"
            safe += parsed.path
            return safe
        except Exception:
            return url.split('@')[-1]   # Just show host part
    return url

logger.info(f"📦 Database backend: {'PostgreSQL' if IS_POSTGRES else 'SQLite'} ({_safe_url_for_log(_database_url)})")

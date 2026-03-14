"""
Database Connection Wrapper
===========================
Provides a unified interface so PostgreSQL connections behave like SQLite connections.
Existing code continues to use `?` placeholders, `INSERT OR REPLACE`, `sqlite3.Row`-style
dict access, etc. — the wrapper converts everything transparently.

Usage:
    from core.db_wrapper import create_connection, create_connection_ctx, IS_POSTGRES

    # Instead of: conn = sqlite3.connect(path)
    conn = create_connection()

    # Instead of: with sqlite3.connect(path) as conn:
    with create_connection_ctx() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM candidates WHERE id = ?", (cid,))
        row = cursor.fetchone()
        print(dict(row))  # Works like sqlite3.Row
"""
import os
import re
import logging
from contextlib import contextmanager
from typing import Optional, List, Any, Tuple, Dict

logger = logging.getLogger(__name__)

# On Cloud Run (K_SERVICE set), default SQLite to /tmp which is a writable tmpfs.
# The overlay filesystem at /app/ causes "disk I/O error" with SQLite.
_default_db = "/tmp/recruitment.db" if os.getenv("K_SERVICE") else "./recruitment.db"
DATABASE_URL = os.getenv("DATABASE_URL", _default_db)
IS_POSTGRES = DATABASE_URL.startswith("postgres")


class DualAccessRow:
    """Row that supports BOTH integer index access (row[0]) and dict-like access (row['col'], dict(row)).
    Mimics sqlite3.Row behavior for PostgreSQL compatibility."""
    
    def __init__(self, values, columns):
        self._values = tuple(values)
        self._columns = tuple(columns)
        self._dict = dict(zip(columns, values))
    
    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._dict[key]
    
    def __len__(self):
        return len(self._values)
    
    def __contains__(self, key):
        return key in self._dict
    
    def __iter__(self):
        # Iterate over VALUES to match sqlite3.Row behavior (enables tuple unpacking)
        return iter(self._values)
    
    def keys(self):
        return self._columns
    
    def values(self):
        return self._values
    
    def items(self):
        return self._dict.items()
    
    def get(self, key, default=None):
        return self._dict.get(key, default)
    
    def __repr__(self):
        return f"DualAccessRow({self._dict})"


def _convert_placeholders_to_pct_s(sql: str) -> str:
    """Convert `?` placeholders to `%s` for psycopg2. Skips `?` inside string literals."""
    result = []
    in_string = False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
            result.append(ch)
        elif ch == '?' and not in_string:
            result.append('%s')
        else:
            result.append(ch)
    return ''.join(result)


def _convert_sqlite_functions(sql: str) -> str:
    """Convert SQLite-specific function calls to PostgreSQL equivalents."""
    # Skip PRAGMAs
    if sql.strip().upper().startswith('PRAGMA'):
        return None  # Signal to skip this query
    
    # datetime('now') → NOW()
    sql = re.sub(r"datetime\s*\(\s*'now'\s*\)", "NOW()", sql, flags=re.IGNORECASE)
    
    # datetime('now', '-N days/hours') → NOW() - INTERVAL 'N days'
    sql = re.sub(
        r"datetime\s*\(\s*'now'\s*,\s*'(-\d+)\s+(day|hour|minute|second)s?'\s*\)",
        r"NOW() + INTERVAL '\1 \2s'",
        sql,
        flags=re.IGNORECASE
    )
    
    # datetime(column) → (column)::timestamp  (must be AFTER the datetime('now') rules)
    sql = re.sub(r"\bdatetime\((\w+)\)", r"(\1)::timestamp", sql, flags=re.IGNORECASE)
    
    # date(column) → (column)::date
    sql = re.sub(r"\bdate\((\w+)\)", r"(\1)::date", sql, flags=re.IGNORECASE)
    
    # BLOB → BYTEA (in DDL)
    sql = re.sub(r'\bBLOB\b', 'BYTEA', sql, flags=re.IGNORECASE)
    
    # AUTOINCREMENT → remove
    sql = re.sub(r'\bAUTOINCREMENT\b', '', sql, flags=re.IGNORECASE)
    
    # LIKE → ILIKE for case-insensitive matching (SQLite LIKE is case-insensitive by default).
    # Use alternation to skip occurrences inside single-quoted string literals.
    sql = re.sub(r"('(?:[^'\\]|\\.)*')|(\bLIKE\b)",
                 lambda m: m.group(1) if m.group(1) else 'ILIKE',
                 sql, flags=re.IGNORECASE)
    
    # GROUP_CONCAT(col) → string_agg(col::text, ',')
    sql = re.sub(r'\bGROUP_CONCAT\((\w+)\)', r"string_agg(\1::text, ',')", sql, flags=re.IGNORECASE)
    
    # SQLite scalar MAX(a, b) → GREATEST(a, b)  (2-arg MAX is not an aggregate)
    sql = re.sub(r'\bMAX\(([^,]+),\s*([^)]+)\)', r'GREATEST(\1, \2)', sql, flags=re.IGNORECASE)
    
    # SQLite scalar MIN(a, b) → LEAST(a, b)
    sql = re.sub(r'\bMIN\(([^,]+),\s*([^)]+)\)', r'LEAST(\1, \2)', sql, flags=re.IGNORECASE)
    
    return sql


def _convert_insert_or_replace(sql: str) -> str:
    """Convert INSERT OR REPLACE INTO to PostgreSQL ON CONFLICT DO UPDATE.
    Uses a balanced-paren scanner to handle nested function calls in VALUES."""
    stripped = sql.strip()
    # Match up to VALUES keyword (column list uses simple regex since it has no nested parens)
    header_match = re.match(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )
    if not header_match:
        return sql

    table = header_match.group(1)
    columns_str = header_match.group(2)
    columns = [c.strip() for c in columns_str.split(',')]

    # Balanced-paren scan for VALUES (...)
    values_start = header_match.end()
    if values_start >= len(stripped) or stripped[values_start] != '(':
        return sql
    depth = 0
    values_end = values_start
    for i in range(values_start, len(stripped)):
        if stripped[i] == '(':
            depth += 1
        elif stripped[i] == ')':
            depth -= 1
            if depth == 0:
                values_end = i
                break
    if depth != 0:
        return sql
    values_str = stripped[values_start + 1:values_end]

    # Determine conflict column based on known schema
    conflict_map = {
        'candidates': 'id',
        'resumes': 'candidate_id',
        'ai_score_cache': 'candidate_id, job_id',
        'email_processing_log': 'message_id',
        'sync_metadata': 'key',
        'job_descriptions': 'id',
        'users': 'id',
        'search_history': 'id',
    }
    conflict_col = conflict_map.get(table, columns[0])
    conflict_cols_list = [c.strip() for c in conflict_col.split(',')]
    update_cols = [c for c in columns if c not in conflict_cols_list]

    if update_cols:
        update_set = ', '.join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        return (f"INSERT INTO {table} ({columns_str}) VALUES ({values_str})"
                f" ON CONFLICT ({conflict_col}) DO UPDATE SET {update_set}")
    else:
        return (f"INSERT INTO {table} ({columns_str}) VALUES ({values_str})"
                f" ON CONFLICT ({conflict_col}) DO NOTHING")


def convert_sql(sql: str) -> Optional[str]:
    """Full SQL conversion pipeline: SQLite → PostgreSQL.
    Returns None if the query should be skipped (e.g., PRAGMA).
    """
    if not IS_POSTGRES:
        return sql
    
    # Convert SQLite functions
    sql = _convert_sqlite_functions(sql)
    if sql is None:
        return None  # PRAGMA — skip
    
    # Convert INSERT OR REPLACE
    if re.search(r'INSERT\s+OR\s+REPLACE', sql, re.IGNORECASE):
        sql = _convert_insert_or_replace(sql)
    
    # Convert INSERT OR IGNORE
    if re.search(r'INSERT\s+OR\s+IGNORE', sql, re.IGNORECASE):
        sql = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    
    # Convert ? placeholders to %s
    sql = _convert_placeholders_to_pct_s(sql)
    
    return sql


class PgCursorWrapper:
    """Wraps a psycopg2 cursor to auto-convert SQLite SQL to PostgreSQL.
    Returns DualAccessRow for full sqlite3.Row compatibility."""
    
    def __init__(self, cursor, columns=None):
        self._cursor = cursor
        self._columns = columns
    
    def execute(self, sql: str, params: Any = None) -> 'PgCursorWrapper':
        converted = convert_sql(sql)
        if converted is None:
            return self  # Skip PRAGMAs
        try:
            self._cursor.execute(converted, params)
            # Capture column names from cursor description
            if self._cursor.description:
                self._columns = tuple(col[0] for col in self._cursor.description)
        except Exception as e:
            logger.error(f"SQL error: {e}\nQuery: {converted[:200]}")
            raise
        return self
    
    def executemany(self, sql: str, params_list: list) -> 'PgCursorWrapper':
        converted = convert_sql(sql)
        if converted is None:
            return self
        self._cursor.executemany(converted, params_list)
        if self._cursor.description:
            self._columns = tuple(col[0] for col in self._cursor.description)
        return self
    
    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._columns:
            return DualAccessRow(row, self._columns)
        return row
    
    def fetchall(self):
        rows = self._cursor.fetchall()
        if self._columns:
            return [DualAccessRow(r, self._columns) for r in rows]
        return rows
    
    @property
    def lastrowid(self):
        """Return last inserted row ID. For PostgreSQL, only works after INSERT ... RETURNING id."""
        try:
            # psycopg2 exposes lastrowid directly on the cursor for INSERT statements
            raw_id = getattr(self._cursor, 'lastrowid', None)
            if raw_id:
                return raw_id
            # Fallback: if the query used RETURNING, fetch the value
            if self._cursor.description:
                row = self._cursor.fetchone()
                return row[0] if row else None
            return None
        except Exception:
            return None
    
    @property
    def rowcount(self):
        return self._cursor.rowcount
    
    @property
    def description(self):
        return self._cursor.description


class PgConnectionWrapper:
    """Wraps a psycopg2 connection to behave like sqlite3.Connection.
    
    - cursor() returns PgCursorWrapper with auto-SQL-conversion + DualAccessRow
    - execute() auto-converts SQL
    - commit/close/rollback work as expected
    """
    
    def __init__(self, conn):
        self._conn = conn
    
    def cursor(self) -> PgCursorWrapper:
        # Use a standard cursor (not RealDictCursor) — DualAccessRow handles both access patterns
        return PgCursorWrapper(self._conn.cursor())
    
    def execute(self, sql: str, params: Any = None):
        """Execute SQL directly on the connection."""
        converted = convert_sql(sql)
        if converted is None:
            return  # Skip PRAGMAs
        cursor = self._conn.cursor()
        cursor.execute(converted, params)
        return PgCursorWrapper(cursor, 
                               tuple(col[0] for col in cursor.description) if cursor.description else None)
    
    def executemany(self, sql: str, params_list: list):
        converted = convert_sql(sql)
        if converted is None:
            return
        cursor = self._conn.cursor()
        cursor.executemany(converted, params_list)
        return PgCursorWrapper(cursor)
    
    def commit(self):
        self._conn.commit()
    
    def rollback(self):
        self._conn.rollback()
    
    def close(self):
        self._conn.close()
    
    # Context manager support
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        # Don't close — pool may want to reuse
        return False
    
    @property
    def row_factory(self):
        return None
    
    @row_factory.setter
    def row_factory(self, value):
        pass  # We always use RealDictCursor instead


def create_connection(db_url: str = None) -> Any:
    """Create a database connection (SQLite or PostgreSQL based on DATABASE_URL).
    
    When IS_POSTGRES is True (DATABASE_URL starts with postgres), the db_url
    parameter is ignored and DATABASE_URL is always used. This ensures
    production always connects to PostgreSQL even if code passes a SQLite path.
    
    Retries up to 10 times (30s total) for PostgreSQL to handle Cloud SQL proxy startup delay.
    
    Returns a connection that supports:
        conn.cursor(), conn.execute(), conn.commit(), conn.close()
        cursor.execute(sql_with_question_marks, params_tuple)
        row = cursor.fetchone()  → dict-like access: row['column_name'] AND index: row[0]
    """
    if IS_POSTGRES:
        import time
        import psycopg2
        last_err = None
        for attempt in range(10):
            try:
                conn = psycopg2.connect(
                    DATABASE_URL,
                    connect_timeout=5,
                    options='-c statement_timeout=30000',
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )
                if attempt > 0:
                    logger.info(f"✅ PostgreSQL connected on attempt {attempt + 1}")
                return PgConnectionWrapper(conn)
            except psycopg2.OperationalError as e:
                last_err = e
                wait = min(1 + attempt, 5)
                logger.warning(f"⏳ PostgreSQL connection attempt {attempt + 1}/10 failed: {e} — retrying in {wait}s")
                time.sleep(wait)
        raise last_err
    else:
        import sqlite3
        # On Cloud Run, always use DATABASE_URL (/tmp/) regardless of caller-supplied path
        # to avoid "disk I/O error" on the read-only overlay filesystem.
        if os.getenv("K_SERVICE"):
            url = DATABASE_URL
        else:
            url = db_url or DATABASE_URL
        conn = sqlite3.connect(url, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn


@contextmanager
def create_connection_ctx(db_url: str = None):
    """Context manager for a database connection."""
    conn = create_connection(db_url)
    try:
        yield conn
    finally:
        conn.close()


def init_pg_schema(conn):
    """Create all tables and indexes in PostgreSQL (idempotent)."""
    from core.pg_compat import ALL_PG_SCHEMAS, PG_INDEXES
    
    # Use a generous timeout for DDL — Cloud SQL during cold start can be slow
    cursor = conn.cursor()
    cursor.execute("SET statement_timeout = '120s'")
    for ddl in ALL_PG_SCHEMAS:
        cursor.execute(ddl)
    for idx_sql in PG_INDEXES:
        cursor.execute(idx_sql)
    conn.commit()
    
    # Migration: add columns that may be missing from older schema versions
    # Use raw psycopg2 connection with autocommit so each DDL is independent
    raw_conn = conn._conn  # underlying psycopg2 connection (clean after commit above)
    raw_conn.autocommit = True
    raw_cursor = raw_conn.cursor()
    # Check existing columns first (read-only — no locks, avoids ALTER TABLE if unneeded)
    raw_cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'candidates'"
    )
    existing_cols = {row[0] for row in raw_cursor.fetchall()}
    raw_cursor.execute("SET statement_timeout = '10s'")
    raw_cursor.execute("SET lock_timeout = '5s'")
    _migration_columns = [
        ("candidates", "shortlisted_at", "TEXT"),
        ("candidates", "nationality", "TEXT"),
        ("candidates", "notice_period", "TEXT"),
        ("candidates", "current_salary", "TEXT"),
        ("candidates", "expected_salary", "TEXT"),
        ("candidates", "source_portal", "TEXT"),
        ("candidates", "job_applied_for", "TEXT"),
    ]
    for table, col, coltype in _migration_columns:
        if col in existing_cols:
            continue  # column already exists — skip ALTER TABLE
        try:
            raw_cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {coltype}")
            logger.info(f"✅ Migration: added {table}.{col}")
        except Exception as e:
            logger.warning(f"Migration {table}.{col}: {e}")
    raw_cursor.execute("SET statement_timeout = '30s'")
    raw_cursor.execute("SET lock_timeout = '0'")
    raw_cursor.close()
    raw_conn.autocommit = False
    
    # Ensure configurable admin email gets admin role
    admin_email = os.getenv('ADMIN_EMAIL', 'hr@effortz.com')
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = 'admin' WHERE email = %s AND role != 'admin'", (admin_email,))
        conn.commit()
    except Exception as e:
        logger.debug(f"Admin promotion migration: {e}")
    
    logger.info("✅ PostgreSQL schema initialized")


logger.info(f"🔗 DB Wrapper: {'PostgreSQL' if IS_POSTGRES else 'SQLite'}")

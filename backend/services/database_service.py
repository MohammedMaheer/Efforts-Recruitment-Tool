"""
High-Performance Database Service with Connection Pooling
Handles 100,000+ candidates efficiently with caching and async operations
Optimized for concurrent requests
"""
import sqlite3
import json
import re
from typing import List, Dict, Optional
from datetime import datetime
import hashlib
import logging
from contextlib import contextmanager
from threading import Lock

# Database wrapper for SQLite/PostgreSQL compatibility
from core.db_wrapper import create_connection, IS_POSTGRES, init_pg_schema

logger = logging.getLogger(__name__)

# ─── Location cleanup (inline, avoids circular import from db_repair) ─────────
_GARBLED_PARENS_RE = re.compile(r'\s*\([^)]*[\u00c0-\u024f\u0600-\u06ff\u0400-\u04ff\u00d8\u00b1\u00a7\u00a9][^)]*\)')
_NOISE_LOCATIONS = frozenset({'you', 'sir', 'dear', 'n/a', 'na', 'null', 'none', '-', '.', '..', '...', 'unknown', 'soon', 'hello', 'hi', 'hey', 'thanks', 'thank', 'regards', 'resume', 'cv', 'the', 'office', 'our', 'not found', 'not', 'found', 'content provided', 'content', 'provided'})

def _clean_loc(loc: str) -> str:
    """Strip garbled Arabic/Unicode from location strings (fast inline version)."""
    if not loc:
        return ''
    c = _GARBLED_PARENS_RE.sub('', loc).strip()
    c = re.sub(r'[Ø§Ù\u0600-\u06ff\u00c0-\u00ff]{3,}', '', c).strip(' ,;-.()')
    # Remove individual noise words (e.g. "you soon" → "soon" → empty)
    words = [w for w in c.split() if w.lower() not in _NOISE_LOCATIONS]
    c = ' '.join(words).strip(' ,;-.()')
    # Truncate at common non-location tokens (e.g. "Dubai, UAE Valid Driving License")
    _loc_stop = re.search(
        r'\b(valid|visa|licence|license|driving|permit|passport|available|immediate|willing'
        r'|remote|hybrid|operational|management|sql|server|linkedin|github|portfolio|website'
        r'|http|www|com\b|email|address|phone|mobile|resume|cv|experience|years?|certified'
        r'|ensuring|excellent|tracking|alternatives|analytics|supply|chain|consultant'
        r'|assistant|assitant|storekeeper|receptionist|administrative|salesforce|developer'
        r'|engineer|manager|coordinator|specialist|full\s*stack|frontend|backend|devops'
        r'|accountant|technician|analyst|architect|planner|supervisor|operator|microsoft'
        r'|security|support|service|responsible|proficient|seeking|looking|objective'
        r'|summary|profile|education|university|college|bachelor|master|degree'
        r'|english|hindi|arabic|urdu|french|spanish|german|tamil|telugu|malayalam'
        r'|kannada|marathi|bengali|gujarati|punjabi|mandarin|chinese|japanese|korean'
        r'|russian|portuguese|italian|language|languages|fluent|proficiency|native'
        r'|duration|salary|notice|period|current|expected|total|gender|dob|age)\b',
        c, re.IGNORECASE)
    if _loc_stop:
        c = c[:_loc_stop.start()].strip(' ,;-.()')
    # Cap at 80 chars (real locations are short)
    if len(c) > 80:
        c = c[:80].rsplit(',', 1)[0].strip()
    return '' if (c.lower() in _NOISE_LOCATIONS or len(c) <= 1) else c

# ─── CID artifact pattern (PDF font garbage) ─────────────────────────────────
_CID_PATTERN = re.compile(r'\(cid:\d+\)')
_CID_PARTIAL_PATTERN = re.compile(r'\(cid:[^)]*\)')

def _sanitize_phone(phone: str) -> str:
    """Validate and clean phone number. Returns empty string for garbage."""
    if not phone:
        return ''
    phone = phone.strip()
    # Reject CID artifacts
    if 'cid:' in phone.lower():
        return ''
    # Strip CID patterns that may be mixed in
    phone = _CID_PATTERN.sub('', phone).strip()
    phone = _CID_PARTIAL_PATTERN.sub('', phone).strip()
    # Must have 7-15 digits (relaxed to allow country codes and extensions)
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 7 or len(digits) > 18:
        return ''
    # Reject if it's a year (4 digits 19xx/20xx) — only if input is JUST the year
    if re.match(r'^(19|20)\d{2}$', phone.strip()):
        return ''
    # Reject if mostly non-phone chars (allow +, -, ., (, ), space, and digits)
    allowed = set('0123456789+()-. ext#')
    non_phone = sum(1 for c in phone.lower() if c not in allowed)
    if non_phone > len(phone) * 0.2:
        return ''
    return phone

def _sanitize_text_field(val: str) -> str:
    """Strip CID artifacts, control chars, and other garbage from text fields."""
    if not val:
        return val
    # Strip CID font artifacts
    if 'cid:' in val:
        val = _CID_PATTERN.sub('', val)
        val = _CID_PARTIAL_PATTERN.sub('', val)
    # Strip control characters (except newline/tab)
    val = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', val)
    return val.strip()

def sanitize_candidate_data(candidate: Dict) -> Dict:
    """Sanitize all candidate fields before database write.
    Strips CID artifacts, validates phone, cleans location, etc.
    This is the single gateway that prevents garbage from entering the DB."""
    c = dict(candidate)  # Don't mutate the original
    
    # Clean text fields
    for field in ('name', 'summary', 'resume_text', 'raw_email_subject', 'linkedin',
                  'nationality', 'notice_period', 'current_salary', 'expected_salary',
                  'source_portal', 'job_applied_for'):
        val = c.get(field, '')
        if val and isinstance(val, str):
            c[field] = _sanitize_text_field(val)
    
    # Extra name cleanup: collapse spaces, strip trailing single-char "initials",
    # remove numbers/special chars, and proper-case
    name = c.get('name', '')
    if name and isinstance(name, str):
        name = re.sub(r'\s+', ' ', name).strip()
        # Remove trailing single characters (e.g. "Gowtham s" → "Gowtham")
        name = re.sub(r'\s+[a-zA-Z]$', '', name).strip()
        # Remove leading/trailing numbers and special chars
        name = re.sub(r'^[\d\W]+|[\d\W]+$', '', name).strip()
        # Remove embedded numbers (e.g. "Vignesh2002Skk" → "VigneshSkk")
        name = re.sub(r'\d+', '', name).strip()
        # Block single-character or empty names
        if len(name) < 2:
            name = ''
        # Truncate at obvious non-name tokens (slash, comma followed by opening paren, etc.)
        name = re.sub(r'[/,;(].*$', '', name).strip()
        # Truncate at dash followed by digits (e.g. "BI DEVELOPER - 5 YEARS Chennai" → "BI")
        name = re.sub(r'\s*-\s*\d.*$', '', name).strip()
        # Block explicitly bad names
        _blocked_names = frozenset({
            'unknown', 'messages', 'notification', 'noreply', 'no reply',
            'system', 'admin', 'administrator', 'postmaster', 'mailer-daemon',
            'indeed', 'linkedin', 'glassdoor', 'monster', 'info', 'support',
            'test', 'null', 'none', 'n/a', 'na', '',
            'lusha', 'maestrorecruiter', 'maestro recruiter', 'recruiter',
            'hiring manager', 'hiring team', 'dear sir', 'dear madam',
            'candidate', 'applicant', 'resume', 'cv', 'cover letter',
            'naukri', 'bayt', 'gulftalent', 'ziprecruiter', 'careerbuilder',
            'user', 'guest', 'subscriber', 'member',
            'india', 'uae', 'usa', 'uk', 'dubai', 'pakistan', 'qatar',
            'oman', 'bahrain', 'kuwait', 'saudi', 'riyadh', 'jeddah',
            'abu dhabi', 'sharjah', 'ajman', 'chennai', 'mumbai', 'delhi',
            'bangalore', 'hyderabad', 'pune', 'kolkata',
        })
        if name.lower() in _blocked_names:
            name = ''
        # Detect job title used as name — if name contains common job words, discard
        _job_words = {'developer', 'engineer', 'manager', 'analyst', 'designer', 'consultant',
                      'director', 'coordinator', 'specialist', 'intern', 'associate', 'executive',
                      'officer', 'lead', 'architect', 'administrator', 'accountant', 'technician',
                      'assistant', 'storekeeper', 'receptionist', 'secretary', 'clerk',
                      'supervisor', 'operator', 'driver', 'helper', 'advisor', 'planner'}
        name_words_lower = set(name.lower().split())
        if len(name_words_lower & _job_words) >= 1 and len(name_words_lower) <= 5:
            name = ''
        # Block if still <= 1 char after cleaning
        if len(name.strip()) < 2:
            name = ''
        # Title case if all-upper or all-lower
        if name and (name == name.upper() or name == name.lower()):
            name = name.title()
        # Fallback: if name is empty, try to derive from email address
        if not name and c.get('email'):
            email_part = c['email'].split('@')[0]
            # Replace dots, underscores, dashes with spaces
            derived = re.sub(r'[._-]+', ' ', email_part).strip()
            # Remove numeric suffixes and embedded numbers
            derived = re.sub(r'\d+', '', derived).strip()
            # Split camelCase / concatenated words (e.g. "bilalafsar" → "bilal afsar")
            # Insert space before uppercase letters that follow lowercase
            derived = re.sub(r'([a-z])([A-Z])', r'\1 \2', derived)
            # Must have at least 2 chars and not look like a system address
            if len(derived) >= 2 and derived.lower() not in ('info', 'hr', 'admin', 'noreply', 'support', 'contact', 'mail', 'help'):
                name = derived.title()
        c['name'] = name
    
    # Clean phone
    c['phone'] = _sanitize_phone(c.get('phone', ''))
    
    # Clean email
    email = c.get('email', '')
    if email and isinstance(email, str):
        if 'cid:' in email:
            email = _CID_PATTERN.sub('', email).strip()
        # Normalize email for deduplication: strip +tags, lowercase
        email = email.lower().strip()
        if '+' in email.split('@')[0] and '@' in email:
            local, domain = email.rsplit('@', 1)
            local = local.split('+')[0]
            email = f"{local}@{domain}"
        # Normalize Gmail dots (dots are ignored by Gmail)
        if '@' in email:
            local, domain = email.rsplit('@', 1)
            if domain in ('gmail.com', 'googlemail.com'):
                local = local.replace('.', '')
                email = f"{local}@{domain}"
        # Validate basic email format
        if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
            logger.warning(f"Invalid email format in sanitize: {email[:60]}")
            email = ''
        c['email'] = email
    else:
        c['email'] = email or ''
    
    # Clean location
    loc = c.get('location', '')
    if loc and isinstance(loc, str):
        loc = _sanitize_text_field(loc)
        loc = _clean_loc(loc)
        # Remove candidate name from location (e.g. "Suaida Tp Abu Dhabi" with name "Suaida Tp" → "Abu Dhabi")
        cand_name = c.get('name', '')
        if cand_name and loc and cand_name.lower() in loc.lower():
            loc = re.sub(re.escape(cand_name), '', loc, flags=re.IGNORECASE).strip(' ,;-.()')
            loc = _clean_loc(loc)  # re-clean after removal
        c['location'] = loc
    
    # Clean skills list
    skills = c.get('skills', [])
    if isinstance(skills, list):
        clean_skills = []
        for sk in skills:
            sk_str = str(sk).strip()
            if 'cid:' in sk_str:
                sk_str = _CID_PATTERN.sub('', sk_str).strip()
            if sk_str and sk_str not in ('N/A', 'None', '-', ''):
                clean_skills.append(sk_str)
        c['skills'] = clean_skills
    
    # Validate experience is a reasonable number
    exp = c.get('experience', 0)
    try:
        exp = int(float(exp or 0))
        if exp < 0: exp = 0
        if exp > 60: exp = 0  # Likely garbage
        c['experience'] = exp
    except (ValueError, TypeError):
        c['experience'] = 0
    
    return c

class DatabaseService:
    def __init__(self, db_path: str = "./recruitment.db"):
        self.db_path = db_path
        self.connection_lock = Lock()
        self._connection_pool = []
        self._pool_size = 10
        self.init_database()
        logger.info(f"✅ Database initialized with connection pool (size: {self._pool_size})")
    
    @contextmanager
    def get_connection(self):
        """Thread-safe connection pooling with stale connection detection for PostgreSQL"""
        conn = None
        try:
            with self.connection_lock:
                while self._connection_pool:
                    candidate_conn = self._connection_pool.pop()
                    # Validate pooled PG connections (Cloud SQL proxy may close idle ones)
                    if IS_POSTGRES:
                        try:
                            candidate_conn.execute("SELECT 1")
                            conn = candidate_conn
                            break
                        except Exception:
                            try:
                                candidate_conn.close()
                            except Exception:
                                pass
                            continue
                    else:
                        conn = candidate_conn
                        break
                if conn is None:
                    conn = create_connection(self.db_path)
                    if not IS_POSTGRES:
                        # SQLite-specific optimizations
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("PRAGMA synchronous=NORMAL")
                        conn.execute("PRAGMA cache_size=-64000")
                        conn.execute("PRAGMA temp_store=MEMORY")
            
            yield conn

        except Exception:
            # Rollback on error before returning connection to pool
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn:
                with self.connection_lock:
                    if len(self._connection_pool) < self._pool_size:
                        self._connection_pool.append(conn)
                    else:
                        conn.close()
    
    def init_database(self):
        """Initialize database with optimized schema and indexes"""
        if IS_POSTGRES:
            conn = create_connection(self.db_path)
            init_pg_schema(conn)
            conn.close()
            return
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Candidates table with indexes for performance
            cursor.execute("""
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
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Add linkedin column if it doesn't exist (migration for existing DBs)
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN linkedin TEXT")
                logger.info("Added linkedin column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add job_subcategory column if it doesn't exist (migration)
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN job_subcategory TEXT")
                logger.info("Added job_subcategory column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add ai_analysis column for storing detailed AI analysis JSON
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN ai_analysis TEXT")
                logger.info("Added ai_analysis column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add certifications column for storing certifications JSON
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN certifications TEXT")
                logger.info("Added certifications column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add languages column for storing languages JSON
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN languages TEXT")
                logger.info("Added languages column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add resume_text column for storing raw resume text for AI re-analysis
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN resume_text TEXT")
                logger.info("Added resume_text column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add strengths column for persisting AI-generated strengths
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN strengths TEXT")
                logger.info("Added strengths column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add gaps column for persisting AI-generated gaps
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN gaps TEXT")
                logger.info("Added gaps column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # Add shortlisted_at column for audit trail (when candidate was shortlisted)
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN shortlisted_at TEXT")
                logger.info("Added shortlisted_at column to candidates table")
            except Exception:
                pass  # Column already exists
            
            # --- NEW ENRICHED FIELDS (v2.1 migration) ---
            new_columns = [
                ("nationality", "TEXT"),
                ("notice_period", "TEXT"),
                ("current_salary", "TEXT"),
                ("expected_salary", "TEXT"),
                ("source_portal", "TEXT"),
                ("job_applied_for", "TEXT"),
            ]
            for col_name, col_type in new_columns:
                try:
                    cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Added {col_name} column to candidates table")
                except Exception:
                    pass  # Column already exists
            
            # Resume storage table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resumes (
                    candidate_id TEXT PRIMARY KEY,
                    filename TEXT,
                    content_type TEXT,
                    file_data BLOB,
                    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
                )
            """)
            
            # Create indexes for fast lookups (OPTIMIZED)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_hash ON candidates(email_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_category ON candidates(job_category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON candidates(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_last_updated ON candidates(last_updated)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_match_score ON candidates(match_score DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_updated ON candidates(is_active, last_updated)")  # Composite index
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_category_score ON candidates(job_category, match_score DESC)")  # Composite index
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_subcategory ON candidates(job_subcategory)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cat_subcat ON candidates(job_category, job_subcategory)")
            
            # AI Score Cache - prevent reprocessing 10,000s of candidates
            cursor.execute("""
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
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_candidate ON ai_score_cache(candidate_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_job ON ai_score_cache(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_date ON ai_score_cache(cached_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_score ON ai_score_cache(ai_score DESC)")  # For sorting
            
            # Email processing log to track processed messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_processing_log (
                    message_id TEXT PRIMARY KEY,
                    processed_at TEXT,
                    candidate_id TEXT,
                    action TEXT,
                    processing_time_ms INTEGER
                )
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_at ON email_processing_log(processed_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidate_log ON email_processing_log(candidate_id)")
            
            # Sync metadata — persist sync timestamps across restarts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)
            
            # Auto-generated job descriptions
            cursor.execute("""
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
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jd_category ON job_descriptions(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jd_count ON job_descriptions(candidate_count DESC)")
            
            # Search history — track AI searches for reports page
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    description TEXT,
                    result_count INTEGER DEFAULT 0,
                    top_results TEXT,
                    searched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_date ON search_history(searched_at DESC)")
            
            conn.commit()
        
        logger.info("✅ Database initialized with optimized indexes")
    
    def get_connection_raw(self):
        """Get a database connection from the pool. Caller must call return_connection() or close().
        Prefer get_connection() context manager when possible."""
        with self.connection_lock:
            while self._connection_pool:
                candidate_conn = self._connection_pool.pop()
                if IS_POSTGRES:
                    try:
                        candidate_conn.execute("SELECT 1")
                        return candidate_conn
                    except Exception:
                        try:
                            candidate_conn.close()
                        except Exception:
                            pass
                        continue
                else:
                    return candidate_conn
        # Pool exhausted — create new connection
        conn = create_connection(self.db_path)
        if not IS_POSTGRES:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def return_connection(self, conn):
        """Return a connection to the pool instead of closing it."""
        if conn is None:
            return
        with self.connection_lock:
            if len(self._connection_pool) < self._pool_size:
                self._connection_pool.append(conn)
            else:
                conn.close()

    def _release(self, conn):
        """Alias for return_connection — shorter name for use in finally blocks."""
        self.return_connection(conn)
    
    def email_to_hash(self, email: str) -> str:
        """Convert email to hash for fast lookups"""
        return hashlib.sha256(email.lower().strip().encode()).hexdigest()
    
    def get_candidate_by_email(self, email: str) -> Optional[Dict]:
        """Fast lookup by email hash"""
        email_hash = self.email_to_hash(email)
        
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM candidates 
                WHERE email_hash = ? AND is_active = 1
            """, (email_hash,))
            
            row = cursor.fetchone()
            
            if row:
                return self._row_to_candidate(row)
            return None
        finally:
            self.return_connection(conn)
    
    def get_candidate_by_linkedin(self, linkedin_url: str) -> Optional[Dict]:
        """Lookup candidate by LinkedIn profile URL"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Normalize the URL (remove trailing slashes, query params)
            normalized_url = linkedin_url.split('?')[0].rstrip('/')
            
            cursor.execute("""
                SELECT * FROM candidates 
                WHERE linkedin LIKE ? AND is_active = 1
            """, (f"%{normalized_url}%",))
            
            row = cursor.fetchone()
            
            if row:
                return self._row_to_candidate(row)
            return None
        finally:
            self.return_connection(conn)
    
    def get_candidate_by_id(self, candidate_id: str) -> Optional[Dict]:
        """Get a single candidate by their ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM candidates 
                WHERE id = ? AND is_active = 1
            """, (candidate_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_candidate(row)
            return None

    def update_candidate_status(self, candidate_id: str, status: str) -> bool:
        """Update only the status field for a candidate. Records shortlisted_at timestamp when shortlisting."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE candidates SET status = ?, last_updated = ?
                WHERE id = ? AND is_active = 1
            """, (status, now, candidate_id))
            # Record timestamp when candidate is shortlisted for audit trail
            if status.lower() in ('shortlisted', 'shortlist') and cursor.rowcount > 0:
                try:
                    cursor.execute("""
                        UPDATE candidates SET shortlisted_at = ?
                        WHERE id = ? AND is_active = 1
                    """, (now, candidate_id))
                except Exception:
                    pass  # Column may not exist yet — non-critical
            conn.commit()
            return cursor.rowcount > 0

    def get_total_candidates(self) -> int:
        """Get total number of active candidates in database"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1")
            count = cursor.fetchone()[0]
            return count
    
    def clear_all_candidates(self) -> int:
        """Delete all candidates from database. Returns count of deleted records."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Get count before deletion
            cursor.execute("SELECT COUNT(*) FROM candidates")
            count = cursor.fetchone()[0]
            
            # Delete all candidates
            cursor.execute("DELETE FROM candidates")
            
            # Also clear resumes
            try:
                cursor.execute("DELETE FROM resumes")
            except Exception:
                pass
            
            # Also clear the AI score cache
            cursor.execute("DELETE FROM ai_score_cache")
            
            # Also clear email processing log
            cursor.execute("DELETE FROM email_processing_log")
            
            conn.commit()
            
            logger.info(f"🗑️ Cleared {count} candidates from database")
            return count
        finally:
            self.return_connection(conn)
    
    # ── Blocked email patterns (Indeed relay, system emails) ──
    BLOCKED_EMAIL_PATTERNS = [
        r'@indeedemail\.com$',
        r'^conversation-.*@',
        r'^[a-f0-9]{32,}@',           # hash-style relay addresses
        r'^employer.*noreply@',
        r'^.+-[a-f0-9]{8,}@indeedemail',
        r'^systemgenerated@',
        r'@zohosalesiq\.',
        r'@zohocrm\.',
        r'@freshdesk\.',
        r'@zendesk\.',
        # Generic portal forwarding addresses — these are portal inboxes, not candidate emails
        r'^cv@',
        r'^resume@',
        r'^resumes@',
        r'^careers@',
        r'^jobs@',
        r'^apply@',
        r'^applications@',
        r'^recruitment@',
        r'^hiring@',
        r'^talent@',
        r'^candidates@',
    ]
    
    @staticmethod
    def is_blocked_email(email_addr: str) -> bool:
        """Check if an email address matches blocked patterns (Indeed relay, system, etc.)"""
        if not email_addr:
            return True
        email_lower = email_addr.lower().strip()
        # Fast exact-domain check
        if email_lower.endswith('@indeedemail.com'):
            return True
        # Regex patterns
        import re
        for pattern in DatabaseService.BLOCKED_EMAIL_PATTERNS:
            if re.search(pattern, email_lower):
                return True
        return False
    
    def purge_indeed_candidates(self) -> dict:
        """Delete all candidates with Indeed relay emails (@indeedemail.com, conversation-*)"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Count before
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE '%@indeedemail.com'")
            indeed_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE 'conversation-%'")
            convo_count = cursor.fetchone()[0]
            
            # Delete Indeed relay emails
            cursor.execute("DELETE FROM candidates WHERE email LIKE '%@indeedemail.com'")
            
            # Delete conversation-id style emails
            cursor.execute("DELETE FROM candidates WHERE email LIKE 'conversation-%'")
            
            # Delete candidates whose name looks like a conversation ID
            cursor.execute("DELETE FROM candidates WHERE name LIKE '%conversation-%'")
            
            # Delete candidates whose name contains 'indeedemail'
            cursor.execute("DELETE FROM candidates WHERE name LIKE '%indeedemail%'")
            
            # Count after
            cursor.execute("SELECT COUNT(*) FROM candidates")
            remaining = cursor.fetchone()[0]
            
            conn.commit()
            
            total_deleted = indeed_count + convo_count
            logger.info(f"🗑️ Purged Indeed relay candidates: {total_deleted} deleted, {remaining} remaining")
            return {
                'indeed_emails_deleted': indeed_count,
                'conversation_ids_deleted': convo_count,
                'total_deleted': total_deleted,
                'remaining_candidates': remaining
            }
        finally:
            self.return_connection(conn)
    
    def insert_candidate(self, candidate: Dict):
        """Insert new candidate (or merge if exists). Blocks Indeed relay emails."""
        # Sanitize all fields before writing — prevent CID artifacts and garbage
        candidate = sanitize_candidate_data(candidate)
        
        # Smart filter: block Indeed relay / garbage emails at DB level
        candidate_email = candidate.get('email', '')
        if self.is_blocked_email(candidate_email):
            logger.info(f"🚫 Blocked insert for Indeed relay email: {candidate_email[:50]}")
            return
        
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            email_hash = self.email_to_hash(candidate['email'])
            
            # Check if candidate already exists — merge instead of silently ignoring
            cursor.execute("SELECT id FROM candidates WHERE email_hash = ?", (email_hash,))
            existing = cursor.fetchone()
            if existing:
                # Candidate exists — use smart merge to preserve existing data
                existing_id = existing[0]
                self.return_connection(conn)
                existing_data = self.get_candidate_by_id(existing_id)
                if existing_data:
                    merged = self.smart_merge_candidate(existing_data, candidate)
                    merged['id'] = existing_id
                    logger.info(f"📝 Candidate exists, smart-merging: {candidate.get('name', 'Unknown')} ({candidate_email[:40]})")
                    return self.update_candidate(merged)
                else:
                    candidate['id'] = existing_id
                    return self.update_candidate(candidate)
            
            # Handle education - ensure it's JSON string
            education_data = candidate.get('education', '[]')
            if isinstance(education_data, list):
                education_data = json.dumps(education_data)
            elif not education_data:
                education_data = '[]'
            
            cursor.execute("""
                INSERT INTO candidates (
                    id, email, email_hash, name, phone, location, 
                    skills, experience, education, summary, work_history,
                    linkedin, status, match_score, job_category, job_subcategory,
                    applied_date, last_updated, raw_email_subject,
                    certifications, languages, resume_text,
                    nationality, notice_period, current_salary, expected_salary,
                    source_portal, job_applied_for
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate['id'],
                candidate['email'],
                email_hash,
                candidate['name'],
                candidate.get('phone', ''),
                candidate.get('location', ''),
                json.dumps(candidate.get('skills', [])),
                candidate.get('experience', 0),
                education_data,
                candidate.get('summary', ''),
                json.dumps(candidate.get('workHistory') or candidate.get('work_history') or []),
                candidate.get('linkedin', ''),
                candidate.get('status', 'New'),
                candidate.get('matchScore') or 0,  # 0 = unscored, never assign fake score
                candidate.get('job_category', 'General'),
                candidate.get('job_subcategory', ''),
                candidate.get('appliedDate'),
                candidate.get('last_updated'),
                candidate.get('raw_email_subject', ''),
                json.dumps(candidate.get('certifications', [])),
                json.dumps(candidate.get('languages', [])),
                candidate.get('resume_text', ''),
                candidate.get('nationality', ''),
                candidate.get('notice_period', ''),
                candidate.get('current_salary', ''),
                candidate.get('expected_salary', ''),
                candidate.get('source_portal', 'Direct'),
                candidate.get('job_applied_for', ''),
            ))
            
            conn.commit()
        finally:
            self.return_connection(conn)
    
    def save_ai_analysis(self, candidate_id: str, analysis: Dict):
        """Save detailed AI analysis for a candidate and update strengths/gaps"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE candidates SET ai_analysis = ? WHERE id = ?",
                (json.dumps(analysis, default=str), candidate_id)
            )
            # Also persist AI pros/cons into strengths/gaps columns for fast access
            strengths = analysis.get('pros') or analysis.get('strengths') or []
            gaps = analysis.get('cons') or analysis.get('weaknesses') or []
            if strengths or gaps:
                cursor.execute(
                    "UPDATE candidates SET strengths = ?, gaps = ? WHERE id = ?",
                    (json.dumps(strengths[:5], default=str), json.dumps(gaps[:5], default=str), candidate_id)
                )
            conn.commit()
        finally:
            self.return_connection(conn)
    
    def get_ai_analysis(self, candidate_id: str) -> Optional[Dict]:
        """Get stored AI analysis for a candidate"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT ai_analysis FROM candidates WHERE id = ?", (candidate_id,))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    return None
            return None
        finally:
            self.return_connection(conn)
    
    def update_candidate(self, candidate: Dict):
        """Update existing candidate (merge new data)"""
        # Sanitize all fields before writing
        candidate = sanitize_candidate_data(candidate)
        
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Handle education - ensure it's JSON string
            education_data = candidate.get('education', '[]')
            if isinstance(education_data, list):
                education_data = json.dumps(education_data)
            elif not education_data:
                education_data = '[]'
            
            cursor.execute("""
                UPDATE candidates SET
                    name = ?,
                    phone = ?,
                    location = ?,
                    skills = ?,
                    experience = ?,
                    education = ?,
                    summary = ?,
                    work_history = ?,
                    linkedin = ?,
                    status = ?,
                    match_score = ?,
                    job_category = ?,
                    job_subcategory = ?,
                    last_updated = ?,
                    raw_email_subject = ?,
                    certifications = ?,
                    languages = ?,
                    nationality = ?,
                    notice_period = ?,
                    current_salary = ?,
                    expected_salary = ?,
                    source_portal = ?,
                    job_applied_for = ?,
                    resume_text = COALESCE(?, resume_text)
                WHERE id = ?
            """, (
                candidate['name'],
                candidate.get('phone', ''),
                candidate.get('location', ''),
                json.dumps(candidate.get('skills', [])),
                candidate.get('experience', 0),
                education_data,
                candidate.get('summary', ''),
                json.dumps(candidate.get('workHistory') or candidate.get('work_history') or []),
                candidate.get('linkedin', ''),
                candidate.get('status', 'New'),
                candidate.get('matchScore') or 0,
                candidate.get('job_category', 'General'),
                candidate.get('job_subcategory', ''),
                candidate.get('last_updated'),
                candidate.get('raw_email_subject', ''),
                json.dumps(candidate.get('certifications', [])),
                json.dumps(candidate.get('languages', [])),
                candidate.get('nationality', ''),
                candidate.get('notice_period', ''),
                candidate.get('current_salary', ''),
                candidate.get('expected_salary', ''),
                candidate.get('source_portal', 'Direct'),
                candidate.get('job_applied_for', ''),
                candidate.get('resume_text', None),
                candidate['id']
            ))
            
            conn.commit()
        finally:
            self.return_connection(conn)

    # ---- helpers for smart value comparison ----

    def deduplicate_candidates(self) -> Dict:
        """Find and merge duplicate candidates (same email, different case → different IDs).
        Keeps the record with the most data, merges the other into it, then deactivates the dupe."""
        with self.connection_lock:
            conn = self.get_connection_raw()
            try:
                cursor = conn.cursor()
                # Find emails that appear more than once (case-insensitive)
                cursor.execute("""
                    SELECT LOWER(TRIM(email)) as norm_email, GROUP_CONCAT(id) as ids, COUNT(*) as cnt
                    FROM candidates WHERE is_active = 1
                    GROUP BY LOWER(TRIM(email))
                    HAVING COUNT(*) > 1
                """)
                dupes = cursor.fetchall()
                merged_count = 0
                for norm_email, ids_str, cnt in dupes:
                    ids = ids_str.split(',')
                    # Get all duplicate rows
                    placeholders = ','.join(['?'] * len(ids))
                    cursor.execute(f"SELECT * FROM candidates WHERE id IN ({placeholders})", ids)
                    rows = cursor.fetchall()
                    candidates = [self._row_to_candidate(r, check_resume=False) for r in rows]
                    
                    # Pick the one with the most data (highest matchScore, then most skills, then most recent)
                    candidates.sort(key=lambda c: (
                        c.get('matchScore', 0),
                        len(c.get('skills', [])),
                        c.get('resume_text', '') or '',
                        c.get('last_updated', '') or ''
                    ), reverse=True)
                    
                    keep = candidates[0]
                    # Generate the canonical ID (lowercase email md5)
                    canonical_id = hashlib.md5(norm_email.encode()).hexdigest()
                    
                    # Merge all duplicates into the keeper
                    for dupe in candidates[1:]:
                        keep = self.smart_merge_candidate(keep, dupe)
                        # Copy resume if the keeper doesn't have one but dupe does
                        try:
                            cursor.execute("SELECT 1 FROM resumes WHERE candidate_id = ?", (keep['id'],))
                            keeper_has_resume = cursor.fetchone() is not None
                            if not keeper_has_resume:
                                cursor.execute("SELECT * FROM resumes WHERE candidate_id = ?", (dupe['id'],))
                                dupe_resume = cursor.fetchone()
                                if dupe_resume:
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO resumes (candidate_id, filename, content_type, file_data, uploaded_at)
                                        VALUES (?, ?, ?, ?, ?)
                                    """, (keep['id'], dupe_resume[1], dupe_resume[2], dupe_resume[3], dupe_resume[4]))
                        except Exception:
                            pass
                        # Deactivate the duplicate
                        cursor.execute("UPDATE candidates SET is_active = 0 WHERE id = ?", (dupe['id'],))
                    
                    # If keeper ID != canonical ID, we need to update it
                    if keep['id'] != canonical_id:
                        old_id = keep['id']
                        # Update resumes FK
                        cursor.execute("UPDATE resumes SET candidate_id = ? WHERE candidate_id = ?", (canonical_id, old_id))
                        # Delete old row, insert with canonical ID
                        cursor.execute("DELETE FROM candidates WHERE id = ?", (old_id,))
                        keep['id'] = canonical_id
                        keep['email'] = norm_email
                    
                    # Update the keeper with merged data
                    self._update_candidate_row(cursor, keep)
                    merged_count += 1
                
                conn.commit()
                return {'duplicates_found': len(dupes), 'merged': merged_count}
            except Exception as e:
                conn.rollback()
                logger.error(f"Dedup transaction failed, rolled back: {e}")
                raise
            finally:
                self.return_connection(conn)
    
    def _update_candidate_row(self, cursor, candidate: Dict):
        """Update or insert a candidate row using all merged data (includes v2.1 enriched fields)."""
        candidate = sanitize_candidate_data(candidate)
        education_data = candidate.get('education', [])
        if isinstance(education_data, list):
            education_data = json.dumps(education_data)
        cursor.execute("""
            INSERT OR REPLACE INTO candidates (
                id, email, email_hash, name, phone, location, 
                skills, experience, education, summary, work_history,
                linkedin, status, match_score, job_category, job_subcategory,
                applied_date, last_updated, raw_email_subject, is_active,
                certifications, languages, resume_text,
                nationality, notice_period, current_salary, expected_salary,
                source_portal, job_applied_for
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            candidate['id'],
            candidate.get('email', ''),
            self.email_to_hash(candidate.get('email', '')),
            candidate.get('name', ''),
            candidate.get('phone', ''),
            candidate.get('location', ''),
            json.dumps(candidate.get('skills', [])),
            candidate.get('experience', 0),
            education_data,
            candidate.get('summary', ''),
            json.dumps(candidate.get('workHistory') or candidate.get('work_history') or []),
            candidate.get('linkedin', ''),
            candidate.get('status', 'New'),
            candidate.get('matchScore', 0),
            candidate.get('job_category', candidate.get('jobCategory', 'General')),
            candidate.get('job_subcategory', candidate.get('jobSubcategory', '')),
            candidate.get('appliedDate', ''),
            candidate.get('last_updated', datetime.now().isoformat()),
            candidate.get('raw_email_subject', ''),
            json.dumps(candidate.get('certifications', [])),
            json.dumps(candidate.get('languages', [])),
            candidate.get('resume_text', ''),
            candidate.get('nationality', ''),
            candidate.get('notice_period', ''),
            candidate.get('current_salary', ''),
            candidate.get('expected_salary', ''),
            candidate.get('source_portal', 'Direct'),
            candidate.get('job_applied_for', ''),
        ))

    @staticmethod
    def _is_meaningful(value) -> bool:
        """Return True when *value* carries real information."""
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip()) and value.strip().lower() not in ('', 'unknown', 'n/a', 'none', 'general')
        if isinstance(value, (list, dict)):
            return bool(value)
        if isinstance(value, (int, float)):
            return value > 0
        return bool(value)

    @staticmethod
    def _parse_json_safe(raw) -> list | dict:
        """Best-effort parse of a JSON column that might already be a list/dict."""
        if isinstance(raw, (list, dict)):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def smart_merge_candidate(self, existing: Dict, new_data: Dict) -> Dict:
        """Intelligently merge *new_data* into *existing* candidate.

        Rules
        -----
        - For scalar fields (name, phone, location, linkedin): keep whichever is
          non-empty; if both are non-empty keep the *longer / richer* value.
        - For list fields (skills, education, certifications, languages,
          work_history): UNION – deduplicate by lowercase equality.
        - experience: keep the higher value (candidates gain experience).
        - matchScore: keep the higher score; never downgrade a manually-set score.
        - status: keep existing status unless it's 'New' and new data has a
          better one (e.g. AI-scored 'Strong').
        - summary / resume_text: keep the longer text.
        - job_category / job_subcategory: keep existing unless it's 'General'
          and new data has a real category.
        """
        merged = dict(existing)  # start from what we have

        # ---- scalars: prefer non-empty, then longer ----
        for key in ('name', 'phone', 'location', 'linkedin'):
            old_val = existing.get(key, '') or ''
            new_val = new_data.get(key, '') or ''
            if not self._is_meaningful(old_val) and self._is_meaningful(new_val):
                merged[key] = new_val
            elif self._is_meaningful(new_val) and len(new_val) > len(old_val):
                merged[key] = new_val

        # ---- list fields: union / deduplicate ----
        for key in ('skills', 'certifications', 'languages'):
            old_list = self._parse_json_safe(existing.get(key))
            new_list = new_data.get(key, [])
            if isinstance(new_list, str):
                new_list = self._parse_json_safe(new_list)
            if new_list:
                seen = {str(v).lower() for v in old_list}
                for v in new_list:
                    if str(v).lower() not in seen:
                        old_list.append(v)
                        seen.add(str(v).lower())
            merged[key] = old_list

        # education & work_history – union by stringified comparison
        # Handle both 'workHistory' (frontend/API) and 'work_history' (DB) field names
        for key, src_keys in [('education', ['education']), ('workHistory', ['workHistory', 'work_history'])]:
            old_list = self._parse_json_safe(existing.get(key) or existing.get(key.replace('H', '_h').replace('istory', '_history') if 'History' in key else key))
            new_list = []
            for sk in src_keys:
                nl = new_data.get(sk, [])
                if nl:
                    new_list = nl
                    break
            if isinstance(new_list, str):
                new_list = self._parse_json_safe(new_list)
            if new_list:
                old_strs = {json.dumps(e, sort_keys=True).lower() for e in old_list if isinstance(e, dict)}
                for entry in new_list:
                    if isinstance(entry, dict):
                        if json.dumps(entry, sort_keys=True).lower() not in old_strs:
                            old_list.append(entry)
                    elif isinstance(entry, str) and entry not in [str(x) for x in old_list]:
                        old_list.append(entry)
            merged[key] = old_list

        # ---- experience: keep higher ----
        old_exp = existing.get('experience', 0) or 0
        new_exp = new_data.get('experience', 0) or 0
        merged['experience'] = max(int(old_exp), int(new_exp))

        # ---- matchScore: keep higher (never downgrade) ----
        old_score = existing.get('matchScore', 0) or existing.get('match_score', 0) or 0
        new_score = new_data.get('matchScore', 0) or 0
        merged['matchScore'] = max(float(old_score), float(new_score))

        # ---- status: keep existing unless it was default 'New' ----
        old_status = existing.get('status', 'New')
        new_status = new_data.get('status', 'New')
        if old_status in ('New',) and new_status not in ('New',):
            merged['status'] = new_status
        else:
            merged['status'] = old_status  # preserve recruiter's manual status changes

        # ---- summary / resume_text: keep longer ----
        for key in ('summary', 'resume_text'):
            old_txt = existing.get(key, '') or ''
            new_txt = new_data.get(key, '') or ''
            merged[key] = new_txt if len(new_txt) > len(old_txt) else old_txt

        # ---- job_category / job_subcategory: keep real over 'General' ----
        for key in ('job_category', 'job_subcategory'):
            old_cat = existing.get(key, 'General') or 'General'
            new_cat = new_data.get(key, '') or ''
            if old_cat in ('General', '', 'Unknown') and self._is_meaningful(new_cat):
                merged[key] = new_cat
            else:
                merged[key] = old_cat

        # ---- raw_email_subject: keep latest ----
        if self._is_meaningful(new_data.get('raw_email_subject')):
            merged['raw_email_subject'] = new_data['raw_email_subject']

        merged['last_updated'] = datetime.now().isoformat()
        merged['id'] = existing.get('id', new_data.get('id'))
        merged['email'] = existing.get('email', new_data.get('email'))

        return merged

    def get_all_candidates_for_matching(self, filters: Dict = None) -> List[Dict]:
        """
        Get ALL active candidates from the DB for comprehensive AI matching.
        Returns full candidate objects (not paginated) for JD matching, chat, and search.
        """
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            query = "SELECT c.*, CASE WHEN r.candidate_id IS NOT NULL THEN 1 ELSE 0 END AS has_resume_flag FROM candidates c LEFT JOIN resumes r ON c.id = r.candidate_id WHERE c.is_active = 1"
            params = []
            if filters:
                if filters.get('min_experience'):
                    query += " AND c.experience >= ?"
                    params.append(filters['min_experience'])
                if filters.get('job_category'):
                    query += " AND c.job_category = ?"
                    params.append(filters['job_category'])
            query += " ORDER BY c.match_score DESC LIMIT 5000"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            candidates = []
            for row in rows:
                c = self._row_to_candidate(row[:-1], check_resume=False)
                c['hasResume'] = bool(row[-1])
                candidates.append(c)
            return candidates
        finally:
            self.return_connection(conn)

    def get_candidates_for_ai(self, filters: Dict = None, limit: int = 10000) -> List[Dict]:
        """
        Enriched candidate query for AI chat — includes work_history, certifications, languages.
        The AI needs this data to give accurate, detailed answers about candidates.
        """
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            query = """
                SELECT c.id, c.name, c.email, c.skills, c.experience, c.education,
                       c.match_score, c.job_category, c.job_subcategory, c.status, c.location, c.summary,
                       c.work_history, c.certifications, c.languages, c.phone, c.linkedin,
                       c.created_at, c.applied_date,
                       CASE WHEN r.candidate_id IS NOT NULL THEN 1 ELSE 0 END AS has_resume_flag,
                       c.nationality, c.notice_period, c.job_applied_for, c.resume_text
                FROM candidates c
                LEFT JOIN resumes r ON c.id = r.candidate_id
                WHERE c.is_active = 1
            """
            params: list = []
            if filters:
                if filters.get('min_experience'):
                    query += " AND experience >= ?"
                    params.append(filters['min_experience'])
                if filters.get('job_category'):
                    query += " AND job_category = ?"
                    params.append(filters['job_category'])
            query += " ORDER BY match_score DESC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for row in rows:
                skills_raw = row[3]
                edu_raw = row[5]
                wh_raw = row[12]
                cert_raw = row[13]
                lang_raw = row[14]
                results.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2],
                    'skills': json.loads(skills_raw) if skills_raw and isinstance(skills_raw, str) else (skills_raw or []),
                    'experience': row[4] or 0,
                    'education': json.loads(edu_raw) if edu_raw and isinstance(edu_raw, str) and edu_raw.startswith('[') else [],
                    'matchScore': row[6] or 0,
                    'match_score': row[6] or 0,
                    'job_category': row[7] or 'General',
                    'job_subcategory': row[8] or '',
                    'status': row[9] or 'New',
                    'location': _clean_loc(row[10] or ''),
                    'summary': row[11] or '',
                    'work_history': json.loads(wh_raw) if wh_raw and isinstance(wh_raw, str) and wh_raw.startswith('[') else [],
                    'certifications': json.loads(cert_raw) if cert_raw and isinstance(cert_raw, str) and cert_raw.startswith('[') else [],
                    'languages': json.loads(lang_raw) if lang_raw and isinstance(lang_raw, str) and lang_raw.startswith('[') else [],
                    'phone': row[15] or '',
                    'linkedin': row[16] or '',
                    'created_at': row[17] or '',
                    'applied_date': row[18] or '',
                    'hasResume': bool(row[19]) if len(row) > 19 else False,
                    'nationality': row[20] if len(row) > 20 else '',
                    'notice_period': row[21] if len(row) > 21 else '',
                    'job_applied_for': row[22] if len(row) > 22 else '',
                    'resume_text': (row[23] or '')[:2000] if len(row) > 23 else '',  # Truncate for memory
                })
            return results
        finally:
            self.return_connection(conn)

    def get_candidates_lightweight(self, filters: Dict = None, limit: int = 500) -> List[Dict]:
        """
        Lightweight candidate query for AI matching — fetches only essential columns.
        Avoids loading resume_text, work_history, ai_analysis for much lower memory/IO.
        """
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            query = """
                SELECT id, name, email, skills, experience, education,
                       match_score, job_category, job_subcategory, status, location, summary
                FROM candidates WHERE is_active = 1
            """
            params: list = []
            if filters:
                if filters.get('min_experience'):
                    query += " AND experience >= ?"
                    params.append(filters['min_experience'])
                if filters.get('job_category'):
                    query += " AND job_category = ?"
                    params.append(filters['job_category'])
            query += " ORDER BY match_score DESC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for row in rows:
                skills_raw = row[3]
                edu_raw = row[5]
                results.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2],
                    'skills': json.loads(skills_raw) if skills_raw and isinstance(skills_raw, str) else (skills_raw or []),
                    'experience': row[4] or 0,
                    'education': json.loads(edu_raw) if edu_raw and isinstance(edu_raw, str) and edu_raw.startswith('[') else [],
                    'matchScore': row[6] or 0,
                    'match_score': row[6] or 0,
                    'job_category': row[7] or 'General',
                    'job_subcategory': row[8] or '',
                    'status': row[9] or 'New',
                    'location': _clean_loc(row[10] or ''),
                    'summary': row[11] or '',
                })
            return results
        finally:
            self.return_connection(conn)

    def _qualify_where(self, where_clause: str) -> str:
        """Prefix bare column names with 'c.' for use in JOIN queries."""
        import re
        # Columns that appear in WHERE filters — prefix with c. if not already qualified
        cols = ['is_active', 'job_subcategory', 'job_category', 'match_score',
                'experience', 'name', 'email', 'skills', 'last_updated',
                'summary', 'location', 'work_history', 'job_applied_for', 'status']
        result = where_clause
        for col in cols:
            # Use word boundaries to avoid partial replacements (e.g. job_category inside job_subcategory)
            result = re.sub(rf'(?<!\w)(?<!c\.){col}(?!\w)', f'c.{col}', result)
        return result

    def get_candidates_paginated(self, page: int = 1, limit: int = 50, filters: Dict = None):
        """Get candidates with pagination, ranked by AI score within job categories"""
        offset = (page - 1) * limit
        
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Build WHERE clause for both count and data queries
            where_clause = "WHERE is_active = 1"
            params = []
            
            if filters:
                if filters.get('status'):
                    where_clause += " AND status = ?"
                    params.append(filters['status'])
                
                if filters.get('job_category'):
                    where_clause += " AND job_category = ?"
                    params.append(filters['job_category'])
                
                if filters.get('job_subcategory'):
                    where_clause += " AND job_subcategory = ?"
                    params.append(filters['job_subcategory'])
                
                if filters.get('min_score'):
                    where_clause += " AND match_score >= ?"
                    params.append(filters['min_score'])
                
                if filters.get('min_experience'):
                    where_clause += " AND experience >= ?"
                    params.append(filters['min_experience'])
                
                if filters.get('search'):
                    where_clause += " AND (name LIKE ? OR email LIKE ? OR skills LIKE ? OR job_subcategory LIKE ? OR summary LIKE ? OR location LIKE ? OR work_history LIKE ? OR job_applied_for LIKE ?)"
                    search_term = f"%{filters['search']}%"
                    params.extend([search_term] * 8)
            
            # Get total count (same filters, no LIMIT/OFFSET)
            cursor.execute(f"SELECT COUNT(*) FROM candidates {where_clause}", params)
            total_count = cursor.fetchone()[0]
            
            # Get paginated data — include hasResume via LEFT JOIN (avoids N+1 queries)
            qualified_where = self._qualify_where(where_clause)
            query = f"""SELECT c.*, CASE WHEN r.candidate_id IS NOT NULL THEN 1 ELSE 0 END AS has_resume_flag
                        FROM candidates c LEFT JOIN resumes r ON c.id = r.candidate_id
                        {qualified_where}"""
            query += " ORDER BY c.job_category ASC, c.match_score DESC, c.last_updated DESC LIMIT ? OFFSET ?"
            data_params = params + [limit, offset]
            
            cursor.execute(query, data_params)
            rows = cursor.fetchall()
            
            candidates = []
            for row in rows:
                c = self._row_to_candidate(row[:-1], check_resume=False)
                c['hasResume'] = bool(row[-1])
                candidates.append(c)
            return candidates, total_count
        finally:
            self.return_connection(conn)

    def get_candidates_light(self, page: int = 1, limit: int = 500, filters: Dict = None):
        """Lightweight candidate listing — minimal columns for list/card views.
        Returns ~1KB per candidate instead of ~10KB."""
        offset = (page - 1) * limit
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            where_clause = "WHERE is_active = 1"
            params = []
            if filters:
                if filters.get('status'):
                    where_clause += " AND status = ?"
                    params.append(filters['status'])
                if filters.get('job_category'):
                    where_clause += " AND job_category = ?"
                    params.append(filters['job_category'])
                if filters.get('job_subcategory'):
                    where_clause += " AND job_subcategory = ?"
                    params.append(filters['job_subcategory'])
                if filters.get('min_score'):
                    where_clause += " AND match_score >= ?"
                    params.append(filters['min_score'])
                if filters.get('min_experience'):
                    where_clause += " AND experience >= ?"
                    params.append(filters['min_experience'])
                if filters.get('search'):
                    where_clause += " AND (name LIKE ? OR email LIKE ? OR skills LIKE ? OR job_subcategory LIKE ? OR summary LIKE ? OR location LIKE ? OR work_history LIKE ? OR job_applied_for LIKE ?)"
                    search_term = f"%{filters['search']}%"
                    params.extend([search_term] * 8)

            cursor.execute(f"SELECT COUNT(*) FROM candidates {where_clause}", params)
            total_count = cursor.fetchone()[0]

            # Minimal columns — skip education, workHistory, summary, resume_text, ai_analysis
            # Include hasResume via LEFT JOIN for efficient check
            cols = ("c.id, c.name, c.email, c.phone, c.location, c.skills, c.experience, "
                    "c.match_score, c.status, c.job_category, c.job_subcategory, c.applied_date, c.linkedin, "
                    "CASE WHEN r.candidate_id IS NOT NULL THEN 1 ELSE 0 END AS has_resume_flag")
            qualified_where = self._qualify_where(where_clause)
            query = f"SELECT {cols} FROM candidates c LEFT JOIN resumes r ON c.id = r.candidate_id {qualified_where}"
            query += " ORDER BY c.match_score DESC, c.last_updated DESC LIMIT ? OFFSET ?"
            cursor.execute(query, params + [limit, offset])
            rows = cursor.fetchall()

            candidates = []
            for row in rows:
                try:
                    # Parse skills and take only top 6 to reduce payload
                    raw_skills = json.loads(row[5]) if row[5] else []
                    skills = raw_skills[:6] if isinstance(raw_skills, list) else []
                    candidates.append({
                        'id': row[0],
                        'name': row[1] or 'Unknown',
                        'email': row[2] or '',
                        'phone': row[3] or '',
                        'location': _clean_loc(row[4] or ''),
                        'skills': skills,
                        'experience': row[6] or 0,
                        'matchScore': row[7] if row[7] else 0,
                        'status': row[8] or 'New',
                        'job_category': row[9] or 'General',
                        'jobCategory': row[9] or 'General',
                        'job_subcategory': row[10] or '',
                        'jobSubcategory': row[10] or '',
                        'appliedDate': row[11] or '',
                        'linkedin': row[12] or '',
                        'hasResume': bool(row[13]),  # from LEFT JOIN
                        'summary': '',
                        'education': [],
                        'workHistory': [],
                        'certifications': [],
                        'languages': [],
                    })
                except Exception as e:
                    logger.warning(f"Skipping candidate row: {e}")
            return candidates, total_count
        finally:
            self.return_connection(conn)
    
    def insert_candidates_batch(self, candidates: List[Dict], batch_size: int = 100):
        """
        Bulk insert candidates for high-volume processing (10,000+)
        Uses transactions for speed and atomicity
        """
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        inserted = 0
        updated = 0
        
        try:
            # Process in batches
            for i in range(0, len(candidates), batch_size):
                batch = candidates[i:i + batch_size]
                
                for candidate in batch:
                    # Sanitize before write
                    candidate = sanitize_candidate_data(candidate)
                    
                    # Block Indeed relay / garbage emails
                    candidate_email = candidate.get('email', '')
                    if self.is_blocked_email(candidate_email):
                        logger.debug(f"🚫 Batch: blocked Indeed relay email: {candidate_email[:50]}")
                        continue
                    
                    email_hash = self.email_to_hash(candidate['email'])
                    
                    # Check if exists
                    cursor.execute("SELECT id FROM candidates WHERE email_hash = ?", (email_hash,))
                    existing = cursor.fetchone()
                    
                    # Handle education - ensure it's JSON string
                    education_data = candidate.get('education', '[]')
                    if isinstance(education_data, list):
                        education_data = json.dumps(education_data)
                    elif not education_data:
                        education_data = '[]'
                    
                    if existing:
                        # Update existing — include all v2.1 enriched fields
                        cursor.execute("""
                            UPDATE candidates SET
                                name = ?, phone = ?, location = ?, skills = ?,
                                experience = ?, education = ?, summary = ?,
                                work_history = ?, linkedin = ?, match_score = ?,
                                job_category = ?, job_subcategory = ?, last_updated = ?,
                                certifications = ?, languages = ?,
                                nationality = ?, notice_period = ?,
                                current_salary = ?, expected_salary = ?,
                                source_portal = ?, job_applied_for = ?,
                                resume_text = COALESCE(?, resume_text)
                            WHERE email_hash = ?
                        """, (
                            candidate['name'],
                            candidate.get('phone', ''),
                            candidate.get('location', ''),
                            json.dumps(candidate.get('skills', [])),
                            candidate.get('experience', 0),
                            education_data,
                            candidate.get('summary', ''),
                            json.dumps(candidate.get('workHistory', [])),
                            candidate.get('linkedin', ''),
                            candidate.get('matchScore') or 0,
                            candidate.get('job_category', 'General'),
                            candidate.get('job_subcategory', ''),
                            candidate.get('last_updated'),
                            json.dumps(candidate.get('certifications', [])),
                            json.dumps(candidate.get('languages', [])),
                            candidate.get('nationality', ''),
                            candidate.get('notice_period', ''),
                            candidate.get('current_salary', ''),
                            candidate.get('expected_salary', ''),
                            candidate.get('source_portal', 'Direct'),
                            candidate.get('job_applied_for', ''),
                            candidate.get('resume_text', None),
                            email_hash
                        ))
                        updated += 1
                    else:
                        # Insert new — include all v2.1 enriched fields
                        cursor.execute("""
                            INSERT INTO candidates (
                                id, email, email_hash, name, phone, location, 
                                skills, experience, education, summary, work_history,
                                linkedin, status, match_score, job_category, job_subcategory,
                                applied_date, last_updated, raw_email_subject,
                                certifications, languages, resume_text,
                                nationality, notice_period, current_salary, expected_salary,
                                source_portal, job_applied_for
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            candidate['id'],
                            candidate['email'],
                            email_hash,
                            candidate['name'],
                            candidate.get('phone', ''),
                            candidate.get('location', ''),
                            json.dumps(candidate.get('skills', [])),
                            candidate.get('experience', 0),
                            education_data,
                            candidate.get('summary', ''),
                            json.dumps(candidate.get('workHistory', [])),
                            candidate.get('linkedin', ''),
                            candidate.get('status', 'New'),
                            candidate.get('matchScore') or 0,
                            candidate.get('job_category', 'General'),
                            candidate.get('job_subcategory', ''),
                            candidate.get('appliedDate'),
                            candidate.get('last_updated'),
                            candidate.get('raw_email_subject', ''),
                            json.dumps(candidate.get('certifications', [])),
                            json.dumps(candidate.get('languages', [])),
                            candidate.get('resume_text', ''),
                            candidate.get('nationality', ''),
                            candidate.get('notice_period', ''),
                            candidate.get('current_salary', ''),
                            candidate.get('expected_salary', ''),
                            candidate.get('source_portal', 'Direct'),
                            candidate.get('job_applied_for', ''),
                        ))
                        inserted += 1
                
                # Commit each batch
                conn.commit()
                
                if (i + batch_size) % 1000 == 0:
                    logger.info(f"📊 Batch insert progress: {i + batch_size}/{len(candidates)}")
            
            logger.info(f"✅ Batch complete: {inserted} inserted, {updated} updated")
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Batch insert error: {e}")
            raise
        finally:
            self.return_connection(conn)
        
        return {'inserted': inserted, 'updated': updated}
    
    def get_candidates_stream(self, batch_size: int = 100):
        """
        Generator for streaming large datasets without memory issues
        Yields batches of candidates for processing 10,000+ records
        """
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1")
            total = cursor.fetchone()[0]
            
            offset = 0
            while offset < total:
                cursor.execute("""
                    SELECT * FROM candidates 
                    WHERE is_active = 1 
                    ORDER BY match_score DESC 
                    LIMIT ? OFFSET ?
                """, (batch_size, offset))
                
                rows = cursor.fetchall()
                if not rows:
                    break
                
                yield [self._row_to_candidate(row, check_resume=False) for row in rows]
                offset += batch_size
        finally:
            self.return_connection(conn)
    
    def get_statistics(self) -> Dict:
        """Get database statistics for monitoring"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Total candidates
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1")
            total = cursor.fetchone()[0]
            
            # By category
            cursor.execute("""
                SELECT job_category, COUNT(*), AVG(match_score), MAX(match_score)
                FROM candidates 
                WHERE is_active = 1 
                GROUP BY job_category
            """)
            categories = {}
            for row in cursor.fetchall():
                categories[row[0] or 'General'] = {
                    'count': row[1],
                    'avg_score': round(row[2] or 0, 1),
                    'max_score': round(row[3] or 0, 1)
                }
            
            # By subcategory within each category
            cursor.execute("""
                SELECT job_category, job_subcategory, COUNT(*)
                FROM candidates 
                WHERE is_active = 1 AND job_subcategory IS NOT NULL AND job_subcategory != ''
                GROUP BY job_category, job_subcategory
            """)
            subcategory_stats = {}
            for row in cursor.fetchall():
                cat = row[0] or 'General'
                sub = row[1] or 'Other'
                if cat not in subcategory_stats:
                    subcategory_stats[cat] = {}
                subcategory_stats[cat][sub] = row[2]
            
            # Recent (last 24 hours)
            cursor.execute("""
                SELECT COUNT(*) FROM candidates 
                WHERE is_active = 1 AND datetime(last_updated) > datetime('now', '-1 day')
            """)
            recent = cursor.fetchone()[0]
            
            return {
                'total_candidates': total,
                'categories': categories,
                'subcategories': subcategory_stats,
                'recent_24h': recent
            }
        finally:
            self.return_connection(conn)
    
    def get_new_candidates_since(self, since_date: str) -> List[Dict]:
        """Get only NEW candidates since specific date (incremental processing)"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM candidates 
                WHERE last_updated > ? AND is_active = 1
                ORDER BY last_updated DESC
                LIMIT 5000
            """, (since_date,))
            rows = cursor.fetchall()
            return [self._row_to_candidate(row, check_resume=False) for row in rows]
        finally:
            self.return_connection(conn)
    
    def mark_email_processed(self, message_id: str, candidate_id: str, action: str):
        """Track processed emails to prevent reprocessing"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO email_processing_log 
                (message_id, processed_at, candidate_id, action)
                VALUES (?, ?, ?, ?)
            """, (message_id, datetime.now().isoformat(), candidate_id, action))
            
            conn.commit()
        finally:
            self.return_connection(conn)
    
    def is_email_processed(self, message_id: str) -> bool:
        """Check if email already processed"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 1 FROM email_processing_log WHERE message_id = ?
            """, (message_id,))
            
            result = cursor.fetchone()
            return result is not None
        except Exception:
            return False
        finally:
            self.return_connection(conn)
    
    def get_processed_email_count(self) -> int:
        """Get total number of processed emails in the log"""
        try:
            conn = self.get_connection_raw()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM email_processing_log")
            result = cursor.fetchone()
            self.return_connection(conn)
            return result[0] if result else 0
        except Exception:
            return 0
    
    def get_all_processed_message_ids(self) -> set:
        """Return set of all processed email message_ids for fast cross-verify lookup."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT message_id FROM email_processing_log")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()
        finally:
            self.return_connection(conn)

    def clear_processing_log_since(self, since_date: str) -> int:
        """Delete email_processing_log entries processed on or after since_date (ISO format).
        Returns count of deleted entries. This allows re-processing of previously skipped emails."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM email_processing_log WHERE processed_at >= ?", (since_date,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear processing log since {since_date}: {e}")
            raise
        finally:
            self.return_connection(conn)

    def clear_no_candidate_entries(self) -> int:
        """Delete all email_processing_log entries with action='no-candidate'.
        Allows re-processing emails that previously yielded no candidate."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM email_processing_log WHERE action = 'no-candidate'")
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear no-candidate entries: {e}")
            raise
        finally:
            self.return_connection(conn)

    def clear_all_blocked_entries(self) -> int:
        """Delete ALL blocked/failed entries from email_processing_log.
        This includes no-candidate, blocked-bad-name, blocked-system-email,
        blocked-indeed-relay. Keeps successfully processed entries (inserted/updated)."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM email_processing_log WHERE action NOT IN ('inserted', 'updated')"
            )
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear blocked entries: {e}")
            raise
        finally:
            self.return_connection(conn)

    def clear_orphaned_processing_entries(self) -> int:
        """Delete processing log entries where action='inserted'/'updated' but 
        the referenced candidate no longer exists in the candidates table.
        This handles the case where candidates were lost during DB restore."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            # Find orphaned entries: marked as inserted/updated but candidate_id 
            # doesn't exist in candidates table
            cursor.execute("""
                DELETE FROM email_processing_log 
                WHERE action IN ('inserted', 'updated')
                AND candidate_id != ''
                AND candidate_id IS NOT NULL
                AND candidate_id NOT IN (SELECT id FROM candidates)
            """)
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear orphaned processing entries: {e}")
            raise
        finally:
            self.return_connection(conn)

    def clear_all_processing_entries(self) -> int:
        """Nuclear option: Delete ALL entries from email_processing_log.
        Forces complete re-processing of every email in the inbox."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM email_processing_log")
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception:
            return 0
        finally:
            self.return_connection(conn)

    def get_all_candidate_emails(self) -> list:
        """Return list of (id, email) tuples for all active candidates."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, email FROM candidates WHERE COALESCE(is_active, 1) = 1")
            return [(row[0], row[1]) for row in cursor.fetchall() if row[1]]
        except Exception:
            return []
        finally:
            self.return_connection(conn)

    def get_all_resume_candidate_ids(self) -> set:
        """Return set of candidate_ids that already have a resume stored."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT candidate_id FROM resumes")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()
        finally:
            self.return_connection(conn)

    def get_candidate_message_ids(self) -> list:
        """Return list of (message_id, candidate_id) for emails that produced a candidate."""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT message_id, candidate_id FROM email_processing_log "
                "WHERE candidate_id != '' AND action IN ('inserted', 'updated')"
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception:
            return []
        finally:
            self.return_connection(conn)

    def get_sync_metadata(self, key: str) -> Optional[str]:
        """Get a persisted sync metadata value (e.g. last_email_sync_time)"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM sync_metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception:
            return None
        finally:
            self.return_connection(conn)
    
    def set_sync_metadata(self, key: str, value: str):
        """Persist a sync metadata value"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
            conn.commit()
        finally:
            self.return_connection(conn)
    
    def _row_to_candidate(self, row, check_resume: bool = True) -> Dict:
        """Convert database row to candidate dict using named column access.
        Works with both sqlite3.Row and DualAccessRow (PostgreSQL)."""

        def _g(col: str, default=''):
            """Safe row getter by column name, falling back to default."""
            try:
                val = row[col]
                return val if val is not None else default
            except (KeyError, IndexError, TypeError):
                return default

        def _gj(col: str, default_factory=list):
            """Safe JSON deserialization from a column."""
            raw = _g(col, '')
            if not raw:
                return default_factory()
            try:
                val = json.loads(raw) if isinstance(raw, str) else raw
                return val if isinstance(val, (list, dict)) else default_factory()
            except (json.JSONDecodeError, TypeError):
                return default_factory()

        candidate = {
            'id': _g('id'),
            'email': _g('email'),
            'name': _g('name'),
            'phone': _g('phone'),
            'location': _clean_loc(_g('location')),
            'skills': _gj('skills'),
            'experience': _g('experience', 0) or 0,
            'education': _gj('education'),
            'summary': _g('summary'),
            'workHistory': [],
            'linkedin': _g('linkedin'),
            'status': _g('status', 'New'),
            'matchScore': _g('match_score', 0) or 0,
            'jobCategory': _g('job_category', 'General') or 'General',
            'job_category': _g('job_category', 'General') or 'General',
            'jobSubcategory': _g('job_subcategory', ''),
            'job_subcategory': _g('job_subcategory', ''),
            'appliedDate': _g('applied_date', ''),
            'last_updated': _g('last_updated', ''),
            'raw_email_subject': _g('raw_email_subject', ''),
            'hasResume': False,
        }

        # Work history: map 'period' → 'duration' for frontend compatibility
        raw_work_history = _gj('work_history')
        if isinstance(raw_work_history, list):
            for entry in raw_work_history:
                if isinstance(entry, dict):
                    if 'period' in entry and 'duration' not in entry:
                        entry['duration'] = entry['period']
                    elif 'duration' not in entry:
                        entry['duration'] = ''
            candidate['workHistory'] = raw_work_history

        # AI analysis
        ai_raw = _g('ai_analysis', '')
        ai = None
        if ai_raw:
            try:
                ai = json.loads(ai_raw) if isinstance(ai_raw, str) else ai_raw
                if not isinstance(ai, dict):
                    ai = None
            except (json.JSONDecodeError, TypeError):
                ai = None
        candidate['ai_analysis'] = ai

        # Certifications and languages
        candidate['certifications'] = _gj('certifications')
        candidate['languages'] = _gj('languages')

        # Resume text
        resume_text = _g('resume_text', '')
        candidate['resume_text'] = resume_text

        # Strengths and gaps from dedicated columns
        stored_col_strengths = _gj('strengths')
        stored_col_gaps = _gj('gaps')
        
        # Generate strengths and gaps — priority order:
        # 1) Dedicated columns (persisted from AI analysis)
        # 2) ai_analysis JSON (pros/cons/strengths)
        # 3) Auto-generated from candidate data
        ai = candidate.get('ai_analysis') or {}
        ai_strengths = (ai.get('pros') or ai.get('strengths') or []) if isinstance(ai, dict) else []
        ai_gaps = (ai.get('cons') or ai.get('gaps') or ai.get('weaknesses') or []) if isinstance(ai, dict) else []
        
        best_strengths = stored_col_strengths or ai_strengths
        best_gaps = stored_col_gaps or ai_gaps
        
        if best_strengths:
            candidate['strengths'] = best_strengths[:5]
        else:
            strengths = []
            skills = candidate.get('skills', [])
            exp = candidate.get('experience', 0) or 0
            edu = candidate.get('education', [])
            certs = candidate.get('certifications', [])
            langs = candidate.get('languages', [])
            score = candidate.get('matchScore', 0) or 0
            
            if len(skills) >= 8:
                strengths.append(f"Strong technical profile with {len(skills)} identified skills")
            elif len(skills) >= 4:
                strengths.append(f"Solid skill set covering {len(skills)} technologies")
            if exp >= 10:
                strengths.append(f"Highly experienced professional with {exp}+ years in the industry")
            elif exp >= 5:
                strengths.append(f"{exp} years of professional experience demonstrates solid career progression")
            elif exp >= 2:
                strengths.append(f"{exp} years of relevant professional experience")
            if edu and len(edu) > 0:
                top_edu = edu[0] if isinstance(edu[0], dict) else {}
                degree = top_edu.get('degree', '')
                field = top_edu.get('field', '')
                if degree and field:
                    strengths.append(f"Educational background: {degree} in {field}")
                elif degree:
                    strengths.append(f"Holds a {degree} degree")
            if certs and len(certs) > 0:
                strengths.append(f"Certified: {', '.join(certs[:3])}")
            if langs and len(langs) > 1:
                strengths.append(f"Multilingual: {', '.join(langs[:3])}")
            if candidate.get('linkedin'):
                strengths.append("Active professional network (LinkedIn profile available)")
            if score >= 80:
                strengths.append("Overall profile quality rated as excellent by AI analysis")
            elif score >= 65:
                strengths.append("Above-average profile quality based on AI evaluation")
            candidate['strengths'] = strengths[:5]  # Cap at 5
        
        if best_gaps:
            candidate['gaps'] = best_gaps[:5]
        else:
            gaps = []
            skills = candidate.get('skills', [])
            exp = candidate.get('experience', 0) or 0
            edu = candidate.get('education', [])
            score = candidate.get('matchScore', 0) or 0
            
            if len(skills) < 3:
                gaps.append("Limited skills information available — may need deeper screening")
            if exp == 0:
                gaps.append("Experience level not specified — clarification needed")
            if not edu or len(edu) == 0:
                gaps.append("No formal education details provided")
            if not candidate.get('phone'):
                gaps.append("No phone number on file — email-only contact")
            if not candidate.get('location'):
                gaps.append("Location not specified — remote/relocation status unknown")
            if not candidate.get('linkedin'):
                gaps.append("No LinkedIn profile — limited professional network visibility")
            if not candidate.get('certifications') or len(candidate.get('certifications', [])) == 0:
                gaps.append("No professional certifications listed")
            if score < 50:
                gaps.append("Below-average profile match score — may not meet role requirements")
            candidate['gaps'] = gaps[:5]  # Cap at 5
        
        # Recommendation: prefer AI hiring recommendation, fallback to job category
        ai_recommendation = ''
        if isinstance(ai, dict):
            ai_recommendation = ai.get('hiring_recommendation', '') or ''
            if ai_recommendation:
                ai_recommendation = ai_recommendation.replace('_', ' ')
        candidate['recommendation'] = ai_recommendation or candidate.get('job_category', 'General')
        
        # shortlisted_at
        candidate['shortlisted_at'] = _g('shortlisted_at', '')

        # --- ENRICHED FIELDS (v2.1) ---
        candidate['nationality'] = _g('nationality', '')
        candidate['notice_period'] = _g('notice_period', '')
        candidate['current_salary'] = _g('current_salary', '')
        candidate['expected_salary'] = _g('expected_salary', '')
        candidate['source_portal'] = _g('source_portal', 'Direct') or 'Direct'
        candidate['job_applied_for'] = _g('job_applied_for', '')
        
        # Check if resume exists (optional to avoid N+1 queries)
        if check_resume:
            conn = None
            try:
                conn = self.get_connection_raw()
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM resumes WHERE candidate_id = ?", (candidate['id'],))
                candidate['hasResume'] = cursor.fetchone() is not None
            except Exception:
                pass
            finally:
                if conn:
                    self.return_connection(conn)
        
        return candidate
    
    def get_cached_ai_score(self, candidate_id: str, job_id: str) -> Optional[Dict]:
        """Get cached AI analysis to avoid reprocessing"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT ai_score, strengths, gaps, recommendation, cached_at
                FROM ai_score_cache
                WHERE candidate_id = ? AND job_id = ?
            """, (candidate_id, job_id))
            
            row = cursor.fetchone()
            
            if row:
                return {
                    'score': row[0],
                    'strengths': json.loads(row[1]) if row[1] else [],
                    'gaps': json.loads(row[2]) if row[2] else [],
                    'recommendation': row[3],
                    'cached_at': row[4],
                    'from_cache': True
                }
            return None
        except Exception:
            return None
        finally:
            self.return_connection(conn)
    
    def cache_ai_score(self, candidate_id: str, job_id: str, analysis: Dict):
        """Cache AI analysis result to save tokens"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            # Clean old cache entries (keep 7 days) to prevent unbounded growth
            cursor.execute("DELETE FROM ai_score_cache WHERE cached_at < datetime('now', '-7 days')")
            
            cursor.execute("""
                INSERT OR REPLACE INTO ai_score_cache 
                (candidate_id, job_id, ai_score, strengths, gaps, recommendation, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate_id,
                job_id,
                analysis.get('score', 0),
                json.dumps(analysis.get('strengths', [])),
                json.dumps(analysis.get('gaps', [])),
                analysis.get('recommendation', ''),
                datetime.now().isoformat()
            ))
            
            conn.commit()
        finally:
            self.return_connection(conn)
    
    def get_candidates_needing_ai_analysis(self, job_id: str) -> List[Dict]:
        """
        Get only candidates WITHOUT cached AI scores
        Optimizes token usage - doesn't reprocess 10,000s
        """
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT c.* FROM candidates c
                LEFT JOIN ai_score_cache a ON c.id = a.candidate_id AND a.job_id = ?
                WHERE c.is_active = 1 AND a.candidate_id IS NULL
                ORDER BY c.last_updated DESC
            """, (job_id,))
            
            rows = cursor.fetchall()
            return [self._row_to_candidate(row, check_resume=False) for row in rows]
        finally:
            self.return_connection(conn)
    
    def save_resume(self, candidate_id: str, filename: str, file_data: bytes, content_type: str = 'application/pdf'):
        """Save resume file to database"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO resumes (candidate_id, filename, content_type, file_data, uploaded_at)
                VALUES (?, ?, ?, ?, ?)
            """, (candidate_id, filename, content_type, file_data, datetime.now().isoformat()))
            
            conn.commit()
            logger.info(f"📄 Saved resume for candidate {candidate_id}: {filename}")
        finally:
            self.return_connection(conn)
    
    def get_resume(self, candidate_id: str) -> Optional[Dict]:
        """Get resume file from database"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT filename, content_type, file_data, uploaded_at
                FROM resumes WHERE candidate_id = ?
            """, (candidate_id,))
            
            row = cursor.fetchone()
            
            if row:
                file_data = row[2]
                # psycopg2 returns BYTEA as memoryview — convert to bytes for FastAPI Response
                if isinstance(file_data, memoryview):
                    file_data = bytes(file_data)
                return {
                    'filename': row[0],
                    'content_type': row[1],
                    'file_data': file_data,
                    'uploaded_at': row[3]
                }
            return None
        finally:
            self.return_connection(conn)

    # ── Search History ──────────────────────────────────────────────────
    def save_search(self, search_id: str, query: str, description: str, result_count: int, top_results: list, user_id: str = ""):
        """Save a search to history"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO search_history (id, query, description, result_count, top_results, searched_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (search_id, query, description, result_count, json.dumps(top_results[:100] if isinstance(top_results, list) else []), datetime.now().isoformat(), user_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving search: {e}")
        finally:
            self.return_connection(conn)

    def get_search_history(self, limit: int = 50) -> list:
        """Get recent search history"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            # Ensure table exists (migration for existing DBs)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    description TEXT,
                    result_count INTEGER DEFAULT 0,
                    top_results TEXT,
                    searched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT
                )
            """)
            cursor.execute("""
                SELECT id, query, description, result_count, top_results, searched_at
                FROM search_history ORDER BY searched_at DESC LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                top = []
                try:
                    top = json.loads(row[4]) if row[4] else []
                except Exception:
                    pass
                results.append({
                    'id': row[0],
                    'query': row[1],
                    'description': row[2] or '',
                    'result_count': row[3],
                    'top_results': top,
                    'searched_at': row[5],
                })
            return results
        except Exception as e:
            logger.error(f"Error getting search history: {e}")
            return []
        finally:
            self.return_connection(conn)

    def delete_search_entry(self, entry_id: str) -> bool:
        """Delete a single search history entry by ID"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.execute("DELETE FROM search_history WHERE id = ?", (entry_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting search entry {entry_id}: {e}")
            return False
        finally:
            self.return_connection(conn)

    def clear_search_history(self):
        """Clear all search history"""
        conn = self.get_connection_raw()
        try:
            conn.execute("DELETE FROM search_history")
            conn.commit()
        except Exception as e:
            logger.error(f"Error clearing search history: {e}")
        finally:
            self.return_connection(conn)

    # ── Pipeline Status Counts ──────────────────────────────────────────
    def get_pipeline_counts(self) -> dict:
        """Get candidate counts by status for pipeline dashboard"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status, COUNT(*) FROM candidates
                WHERE is_active = 1
                GROUP BY status
            """)
            counts = {}
            for row in cursor.fetchall():
                counts[row[0] or 'New'] = row[1]
            return counts
        except Exception as e:
            logger.error(f"Error getting pipeline counts: {e}")
            return {}
        finally:
            self.return_connection(conn)

# Singleton
_db_service = None

def get_db_service():
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service

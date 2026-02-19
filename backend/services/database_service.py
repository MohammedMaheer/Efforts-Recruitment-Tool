"""
High-Performance Database Service with Connection Pooling
Handles 100,000+ candidates efficiently with caching and async operations
Optimized for concurrent requests
"""
import sqlite3
import json
from typing import List, Dict, Optional
from datetime import datetime
import hashlib
import logging
from contextlib import contextmanager
from threading import Lock

# Database wrapper for SQLite/PostgreSQL compatibility
from core.db_wrapper import create_connection, IS_POSTGRES, init_pg_schema

logger = logging.getLogger(__name__)

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
        """Thread-safe connection pooling"""
        conn = None
        try:
            with self.connection_lock:
                if self._connection_pool:
                    conn = self._connection_pool.pop()
                else:
                    conn = create_connection(self.db_path)
                    if not IS_POSTGRES:
                        # SQLite-specific optimizations
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("PRAGMA synchronous=NORMAL")
                        conn.execute("PRAGMA cache_size=-64000")
                        conn.execute("PRAGMA temp_store=MEMORY")
            
            yield conn
            
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
            
            conn.commit()
        
        logger.info("✅ Database initialized with optimized indexes")
    
    def get_connection_raw(self):
        """Get a raw database connection (caller must close). Use get_connection() context manager when possible."""
        conn = create_connection(self.db_path)
        if not IS_POSTGRES:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        return conn
    
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
            conn.close()
    
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
            conn.close()
    
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
        """Update only the status field for a candidate"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE candidates SET status = ?, last_updated = ?
                WHERE id = ? AND is_active = 1
            """, (status, datetime.now().isoformat(), candidate_id))
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
            conn.close()
    
    def insert_candidate(self, candidate: Dict):
        """Insert new candidate (or update if exists)"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
            email_hash = self.email_to_hash(candidate['email'])
            
            # Handle education - ensure it's JSON string
            education_data = candidate.get('education', '[]')
            if isinstance(education_data, list):
                education_data = json.dumps(education_data)
            elif not education_data:
                education_data = '[]'
            
            cursor.execute("""
                INSERT OR REPLACE INTO candidates (
                    id, email, email_hash, name, phone, location, 
                    skills, experience, education, summary, work_history,
                    linkedin, status, match_score, job_category, job_subcategory,
                    applied_date, last_updated, raw_email_subject,
                    certifications, languages, resume_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                candidate.get('matchScore', 45),  # Default to 45 if not scored
                candidate.get('job_category', 'General'),
                candidate.get('job_subcategory', ''),
                candidate.get('appliedDate'),
                candidate.get('last_updated'),
                candidate.get('raw_email_subject', ''),
                json.dumps(candidate.get('certifications', [])),
                json.dumps(candidate.get('languages', [])),
                candidate.get('resume_text', ''),
            ))
            
            conn.commit()
        finally:
            conn.close()
    
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
            conn.close()
    
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
            conn.close()
    
    def update_candidate(self, candidate: Dict):
        """Update existing candidate (merge new data)"""
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
                json.dumps(candidate.get('workHistory', [])),
                candidate.get('linkedin', ''),
                candidate.get('status', 'New'),
                candidate.get('matchScore', 50),
                candidate.get('job_category', 'General'),
                candidate.get('job_subcategory', ''),
                candidate.get('last_updated'),
                candidate.get('raw_email_subject', ''),
                json.dumps(candidate.get('certifications', [])),
                json.dumps(candidate.get('languages', [])),
                candidate.get('resume_text', None),
                candidate['id']
            ))
            
            conn.commit()
        finally:
            conn.close()

    # ---- helpers for smart value comparison ----
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
        for key, src_key in [('education', 'education'), ('workHistory', 'workHistory')]:
            old_list = self._parse_json_safe(existing.get(key))
            new_list = new_data.get(src_key, [])
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
            query = "SELECT * FROM candidates WHERE is_active = 1"
            params = []
            if filters:
                if filters.get('min_experience'):
                    query += " AND experience >= ?"
                    params.append(filters['min_experience'])
                if filters.get('job_category'):
                    query += " AND job_category = ?"
                    params.append(filters['job_category'])
            query += " ORDER BY match_score DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_candidate(row, check_resume=False) for row in rows]
        finally:
            conn.close()

    def get_candidates_for_ai(self, filters: Dict = None, limit: int = 10000) -> List[Dict]:
        """
        Enriched candidate query for AI chat — includes work_history, certifications, languages.
        The AI needs this data to give accurate, detailed answers about candidates.
        """
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            query = """
                SELECT id, name, email, skills, experience, education,
                       match_score, job_category, job_subcategory, status, location, summary,
                       work_history, certifications, languages, phone, linkedin,
                       created_at, applied_date
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
                    'matchScore': row[6] or 50,
                    'match_score': row[6] or 50,
                    'job_category': row[7] or 'General',
                    'job_subcategory': row[8] or '',
                    'status': row[9] or 'New',
                    'location': row[10] or '',
                    'summary': row[11] or '',
                    'work_history': json.loads(wh_raw) if wh_raw and isinstance(wh_raw, str) and wh_raw.startswith('[') else [],
                    'certifications': json.loads(cert_raw) if cert_raw and isinstance(cert_raw, str) and cert_raw.startswith('[') else [],
                    'languages': json.loads(lang_raw) if lang_raw and isinstance(lang_raw, str) and lang_raw.startswith('[') else [],
                    'phone': row[15] or '',
                    'linkedin': row[16] or '',
                    'created_at': row[17] or '',
                    'applied_date': row[18] or '',
                })
            return results
        finally:
            conn.close()

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
                    'matchScore': row[6] or 50,
                    'match_score': row[6] or 50,
                    'job_category': row[7] or 'General',
                    'job_subcategory': row[8] or '',
                    'status': row[9] or 'New',
                    'location': row[10] or '',
                    'summary': row[11] or '',
                })
            return results
        finally:
            conn.close()

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
                    where_clause += " AND (name LIKE ? OR email LIKE ? OR skills LIKE ? OR job_subcategory LIKE ?)"
                    search_term = f"%{filters['search']}%"
                    params.extend([search_term, search_term, search_term, search_term])
            
            # Get total count (same filters, no LIMIT/OFFSET)
            cursor.execute(f"SELECT COUNT(*) FROM candidates {where_clause}", params)
            total_count = cursor.fetchone()[0]
            
            # Get paginated data
            query = f"SELECT * FROM candidates {where_clause}"
            query += " ORDER BY job_category ASC, match_score DESC, last_updated DESC LIMIT ? OFFSET ?"
            data_params = params + [limit, offset]
            
            cursor.execute(query, data_params)
            rows = cursor.fetchall()
            
            candidates = [self._row_to_candidate(row) for row in rows]
            return candidates, total_count
        finally:
            conn.close()

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
                    where_clause += " AND (name LIKE ? OR email LIKE ? OR skills LIKE ? OR job_subcategory LIKE ?)"
                    search_term = f"%{filters['search']}%"
                    params.extend([search_term, search_term, search_term, search_term])

            cursor.execute(f"SELECT COUNT(*) FROM candidates {where_clause}", params)
            total_count = cursor.fetchone()[0]

            # Minimal columns — skip education, workHistory, summary, resume_text, ai_analysis
            cols = ("id, name, email, phone, location, skills, experience, "
                    "match_score, status, job_category, job_subcategory, applied_date, linkedin")
            query = f"SELECT {cols} FROM candidates {where_clause}"
            query += " ORDER BY match_score DESC, last_updated DESC LIMIT ? OFFSET ?"
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
                        'location': row[4] or '',
                        'skills': skills,
                        'experience': row[6] or 0,
                        'matchScore': row[7] if row[7] else 50,
                        'status': row[8] or 'New',
                        'job_category': row[9] or 'General',
                        'jobCategory': row[9] or 'General',
                        'job_subcategory': row[10] or '',
                        'jobSubcategory': row[10] or '',
                        'appliedDate': row[11] or '',
                        'linkedin': row[12] or '',
                        'hasResume': False,
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
            conn.close()
    
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
                        # Update existing
                        cursor.execute("""
                            UPDATE candidates SET
                                name = ?, phone = ?, location = ?, skills = ?,
                                experience = ?, education = ?, summary = ?,
                                work_history = ?, linkedin = ?, match_score = ?,
                                job_category = ?, job_subcategory = ?, last_updated = ?
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
                            candidate.get('matchScore', 50),
                            candidate.get('job_category', 'General'),
                            candidate.get('job_subcategory', ''),
                            candidate.get('last_updated'),
                            email_hash
                        ))
                        updated += 1
                    else:
                        # Insert new
                        cursor.execute("""
                            INSERT INTO candidates (
                                id, email, email_hash, name, phone, location, 
                                skills, experience, education, summary, work_history,
                                linkedin, status, match_score, job_category, job_subcategory,
                                applied_date, last_updated, raw_email_subject
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            candidate.get('matchScore', 50),
                            candidate.get('job_category', 'General'),
                            candidate.get('job_subcategory', ''),
                            candidate.get('appliedDate'),
                            candidate.get('last_updated'),
                            candidate.get('raw_email_subject', '')
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
            conn.close()
        
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
                
                yield [self._row_to_candidate(row) for row in rows]
                offset += batch_size
        finally:
            conn.close()
    
    def get_statistics(self) -> Dict:
        """Get database statistics for monitoring"""
        conn = self.get_connection_raw()
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
            WHERE is_active = 1 AND last_updated > datetime('now', '-1 day')
        """)
        recent = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_candidates': total,
            'categories': categories,
            'subcategories': subcategory_stats,
            'recent_24h': recent
        }
    
    def get_new_candidates_since(self, since_date: str) -> List[Dict]:
        """Get only NEW candidates since specific date (incremental processing)"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM candidates 
            WHERE last_updated > ? AND is_active = 1
            ORDER BY last_updated DESC
        """, (since_date,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_candidate(row) for row in rows]
    
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
            conn.close()
    
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
            conn.close()
    
    def get_processed_email_count(self) -> int:
        """Get total number of processed emails in the log"""
        try:
            conn = self.get_connection_raw()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM email_processing_log")
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else 0
        except Exception:
            return 0
    
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
            conn.close()
    
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
            conn.close()
    
    def _row_to_candidate(self, row, check_resume: bool = True) -> Dict:
        """Convert database row to candidate dict"""
        # Column order (with job_subcategory and ai_analysis added):
        # 0: id, 1: email, 2: email_hash, 3: name, 4: phone, 5: location, 
        # 6: skills, 7: experience, 8: education, 9: summary, 10: work_history,
        # 11: linkedin, 12: status, 13: match_score, 14: job_category,
        # 15: job_subcategory, 16: applied_date, 17: last_updated,
        # 18: raw_email_subject, 19: is_active, 20: created_at, 21: ai_analysis
        
        num_cols = len(row)
        
        # Detect schema: if the DB was created without job_subcategory yet, gracefully handle it
        has_subcategory = num_cols >= 21
        
        if has_subcategory:
            subcategory_idx, applied_idx, updated_idx, subject_idx = 15, 16, 17, 18
        else:
            subcategory_idx, applied_idx, updated_idx, subject_idx = None, 15, 16, 17
        
        candidate = {
            'id': row[0],
            'email': row[1],
            'name': row[3],
            'phone': row[4] or '',
            'location': row[5] or '',
            'skills': json.loads(row[6]) if row[6] else [],
            'experience': row[7] or 0,
            'education': json.loads(row[8]) if row[8] and str(row[8]).startswith('[') else [],
            'summary': row[9] or '',
            'workHistory': [],
            'linkedin': row[11] if num_cols > 11 else '',
            'status': row[12] if num_cols > 12 else 'New',
            'matchScore': row[13] if num_cols > 13 and row[13] else 50,
            'jobCategory': row[14] or 'General',
            'job_category': row[14] or 'General',
            'jobSubcategory': row[subcategory_idx] if subcategory_idx is not None and num_cols > subcategory_idx else '',
            'job_subcategory': row[subcategory_idx] if subcategory_idx is not None and num_cols > subcategory_idx else '',
            'appliedDate': row[applied_idx] if num_cols > applied_idx else '',
            'last_updated': row[updated_idx] if num_cols > updated_idx else '',
            'raw_email_subject': row[subject_idx] if num_cols > subject_idx else '',
            'hasResume': False
        }
        
        # Work history: map 'period' → 'duration' for frontend compatibility
        raw_work_history = json.loads(row[10]) if row[10] else []
        if isinstance(raw_work_history, list):
            for entry in raw_work_history:
                if isinstance(entry, dict):
                    # Ensure 'duration' key exists (frontend expects it)
                    if 'period' in entry and 'duration' not in entry:
                        entry['duration'] = entry['period']
                    elif 'duration' not in entry:
                        entry['duration'] = ''
            candidate['workHistory'] = raw_work_history
        
        # ai_analysis is added via ALTER TABLE so it appears at the end
        # Column order after created_at: ai_analysis, certifications, languages, resume_text
        ai_analysis_idx = 21 if has_subcategory else 20
        if num_cols > ai_analysis_idx and row[ai_analysis_idx]:
            try:
                candidate['ai_analysis'] = json.loads(row[ai_analysis_idx])
            except Exception:
                candidate['ai_analysis'] = None
        else:
            candidate['ai_analysis'] = None
        
        # Certifications and languages (added via ALTER TABLE, after ai_analysis)
        try:
            cert_idx = ai_analysis_idx + 1
            lang_idx = ai_analysis_idx + 2
            if num_cols > cert_idx and row[cert_idx]:
                candidate['certifications'] = json.loads(row[cert_idx]) if isinstance(row[cert_idx], str) and row[cert_idx].startswith('[') else []
            else:
                candidate['certifications'] = []
            if num_cols > lang_idx and row[lang_idx]:
                candidate['languages'] = json.loads(row[lang_idx]) if isinstance(row[lang_idx], str) and row[lang_idx].startswith('[') else []
            else:
                candidate['languages'] = []
        except Exception:
            candidate['certifications'] = []
            candidate['languages'] = []
        
        # resume_text (added via ALTER TABLE, after languages)
        try:
            resume_text_idx = ai_analysis_idx + 3
            if num_cols > resume_text_idx and row[resume_text_idx]:
                candidate['resume_text'] = row[resume_text_idx]
            else:
                candidate['resume_text'] = ''
        except Exception:
            candidate['resume_text'] = ''
        
        # strengths and gaps columns (added via ALTER TABLE, after resume_text)
        stored_col_strengths = []
        stored_col_gaps = []
        try:
            strengths_idx = ai_analysis_idx + 4
            gaps_idx = ai_analysis_idx + 5
            if num_cols > strengths_idx and row[strengths_idx]:
                parsed = json.loads(row[strengths_idx]) if isinstance(row[strengths_idx], str) else []
                if isinstance(parsed, list) and len(parsed) > 0:
                    stored_col_strengths = parsed
            if num_cols > gaps_idx and row[gaps_idx]:
                parsed = json.loads(row[gaps_idx]) if isinstance(row[gaps_idx], str) else []
                if isinstance(parsed, list) and len(parsed) > 0:
                    stored_col_gaps = parsed
        except Exception:
            pass
        
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
        
        # Check if resume exists (optional to avoid N+1 queries)
        if check_resume:
            try:
                conn = self.get_connection_raw()
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM resumes WHERE candidate_id = ?", (row[0],))
                candidate['hasResume'] = cursor.fetchone() is not None
                conn.close()
            except Exception:
                pass
        
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
            conn.close()
    
    def cache_ai_score(self, candidate_id: str, job_id: str, analysis: Dict):
        """Cache AI analysis result to save tokens"""
        conn = self.get_connection_raw()
        try:
            cursor = conn.cursor()
            
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
            conn.close()
    
    def get_candidates_needing_ai_analysis(self, job_id: str) -> List[Dict]:
        """
        Get only candidates WITHOUT cached AI scores
        Optimizes token usage - doesn't reprocess 10,000s
        """
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT c.* FROM candidates c
            LEFT JOIN ai_score_cache a ON c.id = a.candidate_id AND a.job_id = ?
            WHERE c.is_active = 1 AND a.candidate_id IS NULL
            ORDER BY c.last_updated DESC
        """, (job_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_candidate(row) for row in rows]
    
    def save_resume(self, candidate_id: str, filename: str, file_data: bytes, content_type: str = 'application/pdf'):
        """Save resume file to database"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO resumes (candidate_id, filename, content_type, file_data, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
        """, (candidate_id, filename, content_type, file_data, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        logger.info(f"📄 Saved resume for candidate {candidate_id}: {filename}")
    
    def get_resume(self, candidate_id: str) -> Optional[Dict]:
        """Get resume file from database"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT filename, content_type, file_data, uploaded_at
            FROM resumes WHERE candidate_id = ?
        """, (candidate_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'filename': row[0],
                'content_type': row[1],
                'file_data': row[2],
                'uploaded_at': row[3]
            }
        return None

# Singleton
_db_service = None

def get_db_service():
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service

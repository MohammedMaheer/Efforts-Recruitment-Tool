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
                    conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    # Performance optimizations
                    conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging
                    conn.execute("PRAGMA synchronous=NORMAL")  # Faster commits
                    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
                    conn.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
            
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
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add job_subcategory column if it doesn't exist (migration)
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN job_subcategory TEXT")
                logger.info("Added job_subcategory column to candidates table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add ai_analysis column for storing detailed AI analysis JSON
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN ai_analysis TEXT")
                logger.info("Added ai_analysis column to candidates table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add certifications column for storing certifications JSON
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN certifications TEXT")
                logger.info("Added certifications column to candidates table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add languages column for storing languages JSON
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN languages TEXT")
                logger.info("Added languages column to candidates table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add resume_text column for storing raw resume text for AI re-analysis
            try:
                cursor.execute("ALTER TABLE candidates ADD COLUMN resume_text TEXT")
                logger.info("Added resume_text column to candidates table")
            except sqlite3.OperationalError:
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
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn
    
    def email_to_hash(self, email: str) -> str:
        """Convert email to hash for fast lookups"""
        return hashlib.md5(email.lower().strip().encode()).hexdigest()
    
    def get_candidate_by_email(self, email: str) -> Optional[Dict]:
        """Fast lookup by email hash"""
        email_hash = self.email_to_hash(email)
        
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM candidates 
            WHERE email_hash = ? AND is_active = 1
        """, (email_hash,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return self._row_to_candidate(row)
        return None
    
    def get_candidate_by_linkedin(self, linkedin_url: str) -> Optional[Dict]:
        """Lookup candidate by LinkedIn profile URL"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        # Normalize the URL (remove trailing slashes, query params)
        normalized_url = linkedin_url.split('?')[0].rstrip('/')
        
        cursor.execute("""
            SELECT * FROM candidates 
            WHERE linkedin LIKE ? AND is_active = 1
        """, (f"%{normalized_url}%",))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return self._row_to_candidate(row)
        return None
    
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
        cursor = conn.cursor()
        
        # Get count before deletion
        cursor.execute("SELECT COUNT(*) FROM candidates")
        count = cursor.fetchone()[0]
        
        # Delete all candidates
        cursor.execute("DELETE FROM candidates")
        
        # Also clear resumes
        try:
            cursor.execute("DELETE FROM resumes")
        except sqlite3.OperationalError:
            pass
        
        # Also clear the AI score cache
        cursor.execute("DELETE FROM ai_score_cache")
        
        # Also clear email processing log
        cursor.execute("DELETE FROM email_processing_log")
        
        conn.commit()
        conn.close()
        
        logger.info(f"🗑️ Cleared {count} candidates from database")
        return count
    
    def insert_candidate(self, candidate: Dict):
        """Insert new candidate (or update if exists)"""
        conn = self.get_connection_raw()
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
        conn.close()
    
    def save_ai_analysis(self, candidate_id: str, analysis: Dict):
        """Save detailed AI analysis for a candidate"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE candidates SET ai_analysis = ? WHERE id = ?",
            (json.dumps(analysis, default=str), candidate_id)
        )
        conn.commit()
        conn.close()
    
    def get_ai_analysis(self, candidate_id: str) -> Optional[Dict]:
        """Get stored AI analysis for a candidate"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        cursor.execute("SELECT ai_analysis FROM candidates WHERE id = ?", (candidate_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                return None
        return None
    
    def update_candidate(self, candidate: Dict):
        """Update existing candidate (merge new data)"""
        conn = self.get_connection_raw()
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
        conn.close()
    
    def get_candidates_paginated(self, page: int = 1, limit: int = 50, filters: Dict = None):
        """Get candidates with pagination, ranked by AI score within job categories"""
        offset = (page - 1) * limit
        
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        query = "SELECT * FROM candidates WHERE is_active = 1"
        params = []
        
        if filters:
            if filters.get('job_category'):
                query += " AND job_category = ?"
                params.append(filters['job_category'])
            
            if filters.get('job_subcategory'):
                query += " AND job_subcategory = ?"
                params.append(filters['job_subcategory'])
            
            if filters.get('min_score'):
                query += " AND match_score >= ?"
                params.append(filters['min_score'])
            
            if filters.get('min_experience'):
                query += " AND experience >= ?"
                params.append(filters['min_experience'])
            
            if filters.get('search'):
                query += " AND (name LIKE ? OR email LIKE ? OR skills LIKE ? OR job_subcategory LIKE ?)"
                search_term = f"%{filters['search']}%"
                params.extend([search_term, search_term, search_term, search_term])
        
        # Order by job category first, then match_score DESC (best candidates first)
        query += " ORDER BY job_category ASC, match_score DESC, last_updated DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        conn.close()
        
        return [self._row_to_candidate(row) for row in rows]
    
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
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO email_processing_log 
            (message_id, processed_at, candidate_id, action)
            VALUES (?, ?, ?, ?)
        """, (message_id, datetime.now().isoformat(), candidate_id, action))
        
        conn.commit()
        conn.close()
    
    def is_email_processed(self, message_id: str) -> bool:
        """Check if email already processed"""
        conn = self.get_connection_raw()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 1 FROM email_processing_log WHERE message_id = ?
        """, (message_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        return result is not None
    
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
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT ai_score, strengths, gaps, recommendation, cached_at
            FROM ai_score_cache
            WHERE candidate_id = ? AND job_id = ?
        """, (candidate_id, job_id))
        
        row = cursor.fetchone()
        conn.close()
        
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
    
    def cache_ai_score(self, candidate_id: str, job_id: str, analysis: Dict):
        """Cache AI analysis result to save tokens"""
        conn = self.get_connection_raw()
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

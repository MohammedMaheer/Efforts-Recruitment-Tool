-- Reset PostgreSQL Database
-- This script clears all candidate data while preserving schema

-- Clear candidate data tables
TRUNCATE TABLE candidates CASCADE;
TRUNCATE TABLE resumes CASCADE;
TRUNCATE TABLE search_history CASCADE;
TRUNCATE TABLE audit_log CASCADE;

-- Reset email sync watermark to fetch entire inbox from scratch
DELETE FROM sync_metadata WHERE key = 'last_email_sync_time';

-- Reset user-facing metadata
DELETE FROM sync_metadata WHERE key IN (
    'last_full_sync_time',
    'last_sync_success',
    'email_scraper_last_run',
    'gemini_cache_hit_rate'
);

-- Verify reset
SELECT 'Candidates after reset:' as status, COUNT(*) as count FROM candidates
UNION ALL
SELECT 'Resumes after reset:', COUNT(*) FROM resumes
UNION ALL
SELECT 'Search history after reset:', COUNT(*) FROM search_history
UNION ALL
SELECT 'Audit logs after reset:', COUNT(*) FROM audit_log;

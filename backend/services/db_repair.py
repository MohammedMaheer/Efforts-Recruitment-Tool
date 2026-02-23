"""
Comprehensive Database Repair & Audit Service
Detects and fixes gibberish, garbled, and corrupted candidate profiles.
Can re-look up original emails to recover damaged records.
"""
import re
import json
import hashlib
import logging
import unicodedata
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional

logger = logging.getLogger("db_repair")

# ─── Detection Patterns ──────────────────────────────────────────────────────

# Mojibake / double-encoded UTF-8
MOJIBAKE_RE = re.compile(
    r'Ã[\x80-\xbf¡-ÿ]|â€[™¢¦œ"\'\-]|Â[\xa0-\xff]|Ã©|Ã¨|Ã¼|Ã¶|Ã¢|Ã£|Â·'
    r'|â€|ÃŸ'
    r'|â\x80\x93|â\x80\x94|â\x80\x98|â\x80\x99|â\x80\x9c|â\x80\x9d'
    r'|Ã\x83|Ã\x82|Ã\x81|Ã\x89|Ã\x88|Ã\x87|Ã\x86|Ã\xa4|Ã\xb6|Ã\xbc'
)

# HTML entities that shouldn't be in text fields
HTML_ENTITY_RE = re.compile(r'&(amp|lt|gt|quot|nbsp|#\d{2,4}|#x[0-9a-fA-F]{2,4});')

# HTML tags in data fields
HTML_TAG_RE = re.compile(r'<(div|span|table|tr|td|th|p|br|html|body|head|style|script|a|img|ul|li|ol|h[1-6]|font|b|i|em|strong)\b[^>]*>', re.I)

# System/non-candidate email patterns
SYSTEM_EMAIL_PATTERNS = [
    r'systemgenerated@', r'@zohosalesiq', r'@zohocrm', r'@zoho\.com',
    r'@freshdesk', r'@zendesk', r'@intercom', r'@tawk\.to', r'@crisp\.chat',
    r'noreply@', r'no-reply@', r'-noreply@', r'donotreply@', r'do-not-reply@',
    r'@uploaded\.local$',
    r'^office365reports@', r'^employers-noreply@', r'security-noreply@',
    r'viva-noreply@', r'messages-noreply@',
    r'@email\.teams\.microsoft', r'@emeaemail\.teams\.microsoft',
    r'^noreply@groups\.google', r'^noreply@getgulfjob',
    r'^noreply@ionos', r'^noreply@bayt',
    r'@sharepoint\.com', r'@notifications\.',
    r'mailer-daemon@', r'postmaster@', r'bounce@', r'bounces@',
    r'@emailoctopus\.com', r'@mailchimp\.com', r'@sendinblue\.com',
    r'@sendgrid\.net', r'@mandrillapp\.com',
    r'^notifications-noreply@', r'^calendar-notification@',
    r'^notification@', r'^alert@', r'^helpdesk@', r'^support@',
    r'@indeedemail\.com', r'^conversation-',
    # Job portals / notification senders
    r'^updates@jobhai\.com$', r'@jobhai\.com', r'@naukri\.com',
    r'@monster\.com', r'@shine\.com', r'@timesjobs\.com',
    r'^alerts@', r'^jobs@', r'^careers@', r'^info@',
    r'@recruitmentbox\.com', r'@lever\.co', r'@greenhouse\.io',
    r'@breezy\.hr', r'@workable\.com', r'@smartrecruiters\.com',
    r'^secretariat@', r'@ysu\.am$',
    r'^Postmaster$',  # raw postmaster without domain
]

# Invalid candidate names (service/product names)
INVALID_NAMES = {
    'salesiq', 'zoho', 'freshdesk', 'zendesk', 'intercom', 'hubspot',
    'salesforce', 'tawk', 'crisp', 'bot', 'chatbot', 'website',
    'system', 'auto-reply', 'systemgenerated', 'notification',
    'office365reports', 'indeed for employers', 'google groups',
    'getgulfjob', 'ionos customer service', 'bayt.com',
    'unknown', 'n/a', 'none', 'test', 'admin', 'user', 'candidate',
    'noreply', 'no-reply', 'mailer-daemon', 'postmaster',
    'linkedin', 'indeed', 'glassdoor', 'microsoft outlook',
    'microsoft teams', 'google calendar', 'applicant',
    'mailenable administrator', 'mail administrator',
    'to complete verification', 'please call directly',
    'unsubscribe', 'subscribe', 'newsletter',
}

# Regex patterns for names that indicate ghost/non-candidate entries
GHOST_NAME_PATTERNS = [
    r'^to\s+(complete|verify|confirm|update|subscribe|unsubscribe)',
    r'^please\s+(call|contact|reply|respond|verify|confirm)',
    r'^(click|tap|visit|open|view)\s+(here|link|this|the)',
    r'^(verify|confirm|activate|complete|update)\s+(your|my|the)',
    r'\b(unsubscribe|opt.?out|manage\s+preferences)\b',
    r'^(web|mail|system|server|database)\s*(admin|administrator|manager|operator)',
    r'\b(auto.?reply|out.?of.?office|away\s+message)\b',
    r'^(dear|hello|hi)\s+(sir|madam|team|all|there)$',
]

# Names that are data artifacts
DATA_ARTIFACT_RE = re.compile(
    r'^(Databases?|Technologies?|Skills?|Tools?|Frameworks?|Languages?'
    r'|Experience|Education|Certifications?|Projects?|References?'
    r'|Summary|Objective|Profile|Resume|CV|Contact)\s*:',
    re.I
)

# Names that look like email addresses
EMAIL_AS_NAME_RE = re.compile(r'^[\w.+-]+@[\w-]+\.[\w.]+$')

# Names that are mostly special characters
SPECIAL_CHAR_NAME_RE = re.compile(r'^[\W\d_]+$')

# Names that look like phone numbers / years 
PHONE_AS_NAME_RE = re.compile(r'^\+?[\d\s\-\(\)]{7,}$')
YEAR_AS_NAME_RE = re.compile(r'^(19|20)\d{2}$')


def is_mojibake(text: str) -> bool:
    """Detect if text contains mojibake/encoding corruption."""
    if not text or len(text) < 3:
        return False
    hits = len(MOJIBAKE_RE.findall(text))
    return hits > 0 and (hits * 2) / len(text) > 0.05


def strip_mojibake_patterns(text: str) -> str:
    """Strip known mojibake byte sequences, preserving readable characters."""
    if not text:
        return text
    result = text
    # Ordered longest → shortest for greedy matching
    patterns = [
        'Ã¢Ã¢â¬Â¢â¬Ã¢â¬Â¢Â¢Ã¢â¬Â¢',  # triple-encoded bullet •
        'Ã¢â¬Â¢Ã¢Ã¢â¬Â¢â¬',             # partial triple
        'Ã¢â¬Â¢',                         # double-encoded bullet •
        'Ã¢â¬â¢',                         # variant bullet
        'Ã¢â¬Å"',                         # double-encoded left "
        'Ã¢â¬\x9d',                       # double-encoded right "
        'Ã¢â¬â',                          # double-encoded dash
        'Ã¢â¬',                            # partial double
        'â€™', 'â€œ', 'â€\x9d', 'â€"', 'â€"', 'â€¢', 'â€¦',  # single-encoded smart punct
        'Ã©', 'Ã¨', 'Ã¼', 'Ã¶', 'Ã¤', 'Ã¢', 'Ã£', 'Ã¡', 'Ã­', 'Ã³', 'Ãº',
        'ÃŸ', 'Ãƒ', 'Ã\x83', 'Ã\x82', 'Ã\x81',
        'Â·', 'Â»', 'Â«', 'Â©', 'Â®', 'Â°', 'Â±', 'Â²', 'Â³', 'Â¶',
        'Â\xa0', 'Â\xad',
    ]
    for pat in patterns:
        result = result.replace(pat, '')
    # Collapse whitespace
    result = re.sub(r'\s+', ' ', result).strip()
    return result


def force_clean_mojibake(text: str) -> str:
    """
    Last-resort cleaning: remove all non-ASCII sequences that look like mojibake,
    keeping only printable text. Preserves letters from common scripts.
    """
    if not text:
        return text
    # Remove runs of known mojibake fragment characters
    # These are individual chars that compose mojibake: Ã Â â ¬ ¢ € ™ etc.
    cleaned = re.sub(
        r'[ÃÂâ¬¢€™¦œ\x80-\x9f]+',
        '',
        text
    )
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def try_fix_encoding(text: str) -> str:
    """Attempt to repair double/triple-encoded UTF-8 text."""
    if not text:
        return text

    # Strategy 1: Iterative cp1252 → UTF-8 decode (handles double/triple encoding)
    current = text
    for _ in range(5):
        try:
            decoded = current.encode('cp1252').decode('utf-8')
            if decoded == current:
                break
            current = decoded
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    if current != text and not is_mojibake(current) and len(current.strip()) > 1:
        return current

    # Strategy 2: Strip known mojibake patterns (for triple-encoded PDF text)
    stripped = strip_mojibake_patterns(text)
    if stripped != text and len(stripped.strip()) > 5 and not is_mojibake(stripped):
        return stripped

    # Strategy 3: latin-1 → UTF-8
    try:
        fixed = text.encode('latin-1').decode('utf-8', errors='ignore')
        if fixed != text and not is_mojibake(fixed) and len(fixed.strip()) > 1:
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    # Strategy 4: Force clean — remove mojibake fragment characters entirely
    cleaned = force_clean_mojibake(text)
    if cleaned and len(cleaned.strip()) > 5 and not is_mojibake(cleaned):
        return cleaned

    return text


def clean_html_from_text(text: str) -> str:
    """Strip HTML tags and decode entities from text."""
    if not text:
        return text
    import html
    # Remove HTML tags
    cleaned = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    cleaned = html.unescape(cleaned)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


# ─── Skill Extraction (keyword-based, no AI needed) ──────────────────────────

COMMON_SKILLS = {
    # Programming
    'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'go', 'rust',
    'ruby', 'php', 'swift', 'kotlin', 'scala', 'r', 'matlab', 'perl',
    'html', 'css', 'sql', 'bash', 'shell', 'powershell',
    # Frameworks
    'react', 'angular', 'vue', 'node.js', 'express', 'django', 'flask',
    'spring', 'spring boot', '.net', 'asp.net', 'rails', 'laravel',
    'fastapi', 'next.js', 'nuxt', 'svelte', 'ember',
    # Data / ML
    'machine learning', 'deep learning', 'tensorflow', 'pytorch', 'keras',
    'scikit-learn', 'pandas', 'numpy', 'data science', 'data analysis',
    'data engineering', 'etl', 'spark', 'hadoop', 'kafka', 'airflow',
    'nlp', 'computer vision', 'llm', 'generative ai',
    # Cloud / DevOps
    'aws', 'azure', 'gcp', 'google cloud', 'docker', 'kubernetes', 'k8s',
    'terraform', 'ansible', 'jenkins', 'ci/cd', 'git', 'github', 'gitlab',
    'linux', 'devops', 'microservices', 'serverless',
    # Databases
    'mysql', 'postgresql', 'mongodb', 'redis', 'elasticsearch',
    'oracle', 'sql server', 'dynamodb', 'cassandra', 'firebase', 'sqlite',
    # Other tech
    'rest api', 'graphql', 'api', 'agile', 'scrum', 'jira',
    'project management', 'cybersecurity', 'networking',
    'power bi', 'tableau', 'excel', 'sap', 'erp', 'crm',
    # Soft skills / domains
    'communication', 'leadership', 'team management', 'problem solving',
    'analytical', 'marketing', 'sales', 'accounting', 'finance',
    'human resources', 'recruitment', 'customer service',
    'supply chain', 'logistics', 'operations', 'quality assurance',
    'testing', 'automation', 'selenium', 'cypress',
    'figma', 'photoshop', 'illustrator', 'ui/ux', 'ux design',
}


def extract_skills_from_text(text: str) -> list:
    """Extract skills from resume/summary text using keyword matching."""
    if not text or len(text.strip()) < 20:
        return []
    text_lower = text.lower()
    found = []
    for skill in COMMON_SKILLS:
        # Word boundary check to avoid false matches
        pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, text_lower):
            # Capitalize properly
            found.append(skill.title() if len(skill) > 3 else skill.upper())
    return sorted(set(found))


def keyword_based_score(candidate: Dict) -> int:
    """
    Calculate a quality score using heuristics (no AI required).
    Returns 30-95 based on profile completeness and content quality.
    """
    score = 30  # base
    name = (candidate.get('name') or '').strip()
    email = (candidate.get('email') or '').strip()
    summary = (candidate.get('summary') or '').strip()
    resume_text = (candidate.get('resume_text') or '').strip()
    phone = (candidate.get('phone') or '').strip()
    skills_raw = candidate.get('skills') or '[]'
    experience = candidate.get('experience') or ''
    education = candidate.get('education') or ''
    work_history = candidate.get('work_history') or ''
    
    try:
        skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
    except Exception:
        skills = []
    
    text = (summary + ' ' + resume_text).strip()
    
    # Name quality (+5)
    if name and len(name) > 3 and ' ' in name:
        score += 5
    elif name and len(name) > 2:
        score += 2
    
    # Has email (+3)
    if email and '@' in email and not email.endswith('@uploaded.local'):
        score += 3
    
    # Has phone (+2)
    if phone and len(phone) > 6:
        score += 2
    
    # Skills (+0-15)
    skill_count = len(skills)
    if skill_count >= 10:
        score += 15
    elif skill_count >= 5:
        score += 10
    elif skill_count >= 3:
        score += 7
    elif skill_count >= 1:
        score += 3
    
    # Text length / quality (+0-20)
    text_len = len(text)
    if text_len > 2000:
        score += 20
    elif text_len > 1000:
        score += 15
    elif text_len > 500:
        score += 10
    elif text_len > 100:
        score += 5
    
    # Experience mentions (+0-10)
    exp_str = str(experience).lower()
    if experience:
        try:
            exp_years = int(re.search(r'(\d+)', exp_str).group(1)) if re.search(r'(\d+)', exp_str) else 0
        except Exception:
            exp_years = 0
        if exp_years >= 10:
            score += 10
        elif exp_years >= 5:
            score += 7
        elif exp_years >= 2:
            score += 5
        elif exp_years >= 1:
            score += 3
    
    # Education (+0-5)
    if education:
        edu_lower = str(education).lower()
        if any(w in edu_lower for w in ('master', 'mba', 'phd', 'doctorate', 'm.sc', 'm.tech')):
            score += 5
        elif any(w in edu_lower for w in ('bachelor', 'b.sc', 'b.tech', 'b.e', 'degree')):
            score += 3
        elif any(w in edu_lower for w in ('diploma', 'certificate', 'associate')):
            score += 2
    
    # Work history (+0-5)
    if work_history:
        try:
            wh = json.loads(work_history) if isinstance(work_history, str) else work_history
            job_count = len(wh) if isinstance(wh, list) else 0
            if job_count >= 3:
                score += 5
            elif job_count >= 1:
                score += 3
        except Exception:
            pass
    
    # Cap at 95
    return min(score, 95)


def extract_name_from_text(text: str) -> Optional[str]:
    """Try to extract a real human name from text content."""
    if not text:
        return None
    patterns = [
        r'(?:my\s+name\s+is|i\s+am|this\s+is|name\s*:\s*)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})',
        r'(?:^|\n)\s*([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\s*(?:\n|$)',
        r'(?:dear\s+(?:sir|madam|recruiter|hiring\s+manager)).*?(?:sincerely|regards|best|thanks)\s*,?\s*\n\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I | re.MULTILINE)
        if m:
            name = m.group(1).strip()
            if len(name) > 3 and not is_mojibake(name):
                return name
    return None


def extract_email_from_text(text: str) -> Optional[str]:
    """Extract a valid email address from text."""
    if not text:
        return None
    m = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', text)
    if m:
        email = m.group(0).strip().lower()
        # Skip system emails
        for pat in SYSTEM_EMAIL_PATTERNS:
            if re.search(pat, email, re.I):
                return None
        return email
    return None


def is_valid_name(name: str) -> bool:
    """Check if a name looks like a real human name."""
    if not name or len(name.strip()) < 2:
        return False
    name = name.strip()
    # Too short
    if len(name) <= 2:
        return False
    # Is a known invalid name
    if name.lower() in INVALID_NAMES:
        return False
    # Looks like email
    if EMAIL_AS_NAME_RE.match(name):
        return False
    # All special chars / digits
    if SPECIAL_CHAR_NAME_RE.match(name):
        return False
    # Phone number or year
    if PHONE_AS_NAME_RE.match(name):
        return False
    if YEAR_AS_NAME_RE.match(name):
        return False
    # Data artifact
    if DATA_ARTIFACT_RE.match(name):
        return False
    # Mostly digits (>50%)
    digit_ratio = sum(1 for c in name if c.isdigit()) / max(len(name), 1)
    if digit_ratio > 0.5:
        return False
    # Mojibake
    if is_mojibake(name):
        return False
    # Teams notification pattern
    if re.match(r'.+ in Teams$', name, re.I):
        return False
    return True


def is_gibberish_profile(candidate: Dict) -> Tuple[bool, str]:
    """
    Comprehensive check — returns (is_gibberish, reason).
    Covers all known patterns of non-candidate data.
    """
    name = (candidate.get('name') or '').strip()
    email = (candidate.get('email') or '').lower().strip()
    summary = (candidate.get('summary') or '')
    resume_text = (candidate.get('resume_text') or '')
    phone = (candidate.get('phone') or '')
    skills_raw = candidate.get('skills') or '[]'
    
    try:
        skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
    except:
        skills = []
    
    # 1. System email
    for pat in SYSTEM_EMAIL_PATTERNS:
        if re.search(pat, email, re.I):
            return True, f'system_email: {email}'
    
    # 2. Invalid name
    if name.lower().strip() in INVALID_NAMES:
        return True, f'invalid_name: {name}'
    
    # 2b. Ghost name patterns (regex-based catch-all)
    for gpat in GHOST_NAME_PATTERNS:
        if re.search(gpat, name, re.I):
            return True, f'ghost_name: {name}'
    
    # 3. Teams notification
    if re.match(r'.+ in Teams$', name, re.I):
        return True, f'teams_notification: {name}'
    
    # 4. Email as name
    if EMAIL_AS_NAME_RE.match(name):
        return True, f'email_as_name: {name}'
    
    # 5. Data artifact name
    if DATA_ARTIFACT_RE.match(name):
        return True, f'data_artifact: {name}'
    
    # 6. Phone/year as name or all digits 
    if PHONE_AS_NAME_RE.match(name) or YEAR_AS_NAME_RE.match(name):
        return True, f'numeric_name: {name}'
    if name and sum(1 for c in name if c.isdigit()) / max(len(name), 1) > 0.5:
        return True, f'mostly_digits: {name}'
    
    # 7. All special chars
    if name and SPECIAL_CHAR_NAME_RE.match(name):
        return True, f'special_chars: {name}'
    
    # 8. Empty / placeholder name
    if not name or name.lower() in ('unknown', 'n/a', '-', 'null', 'none', ''):
        # Could still be fixable if we have email or resume text
        has_useful_data = bool(resume_text.strip()) or bool(summary.strip()) or len(skills) > 2
        if not has_useful_data:
            return True, 'empty_name_no_data'
    
    # 9. Chat transcript
    sum_lower = summary[:500].lower()
    if 'chat transcript' in sum_lower and ('attended by' in sum_lower or 'chat duration' in sum_lower):
        return True, 'chat_transcript_summary'
    
    rt_lower = resume_text[:500].lower()
    if 'chat transcript' in rt_lower and 'operating system' in rt_lower and 'browser' in rt_lower:
        return True, 'chat_transcript_resume'
    
    # 9b. Spam / marketing emails posing as candidates
    if sum_lower:
        spam_indicators = 0
        spam_phrases = [
            'looking for website development', 'web development service',
            'mobile app development', 'seo service', 'digital marketing service',
            'our company', 'our team', 'we offer', 'we provide', 'we specialize',
            'get a free quote', 'free consultation', 'contact us today',
            'special offer', 'limited time', 'discount', 'promotional',
            'unsubscribe', 'opt out', 'manage preferences',
            'this email was sent to', 'you are receiving this',
        ]
        for phrase in spam_phrases:
            if phrase in sum_lower:
                spam_indicators += 1
        if spam_indicators >= 2:
            return True, f'spam_marketing_email (indicators={spam_indicators})'
    
    # 10. Completely empty profile (no skills, no summary, no resume, zero score)
    score = candidate.get('match_score') or 0
    if not skills and not summary.strip() and not resume_text.strip() and score == 0:
        return True, 'completely_empty'
    
    return False, ''


def audit_database(conn) -> Dict[str, Any]:
    """
    Run comprehensive audit on the database and return detailed report.
    Does NOT modify any data.
    """
    cursor = conn.cursor()
    
    report = {
        'total_candidates': 0,
        'active_candidates': 0,
        'issues': {},
        'issue_counts': {},
        'score_distribution': {},
        'category_distribution': {},
        'samples': [],
    }
    
    # Total / active counts
    cursor.execute('SELECT COUNT(*) FROM candidates')
    report['total_candidates'] = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM candidates WHERE is_active = 1 OR is_active IS NULL')
    report['active_candidates'] = cursor.fetchone()[0]
    
    # Score distribution
    cursor.execute("""
        SELECT 
            CASE 
                WHEN match_score >= 80 THEN '80-100'
                WHEN match_score >= 60 THEN '60-79'  
                WHEN match_score >= 40 THEN '40-59'
                WHEN match_score > 0 THEN '1-39'
                ELSE '0_or_null'
            END as bucket, COUNT(*) as cnt 
        FROM candidates WHERE is_active = 1 OR is_active IS NULL
        GROUP BY bucket ORDER BY bucket
    """)
    for row in cursor.fetchall():
        report['score_distribution'][row[0]] = row[1]
    
    # Category distribution
    cursor.execute("""
        SELECT job_category, COUNT(*) as cnt 
        FROM candidates WHERE is_active = 1 OR is_active IS NULL
        GROUP BY job_category ORDER BY cnt DESC LIMIT 20
    """)
    for row in cursor.fetchall():
        report['category_distribution'][row[0] or 'NULL'] = row[1]
    
    # Scan all active candidates for issues
    cursor.execute("""
        SELECT id, email, name, phone, location, skills, experience,
               summary, resume_text, raw_email_subject, match_score,
               job_category, job_subcategory, status
        FROM candidates
        WHERE is_active = 1 OR is_active IS NULL
    """)
    columns = [d[0] for d in cursor.description]
    
    issues = {
        'gibberish': [],
        'mojibake_name': [],
        'mojibake_text': [],
        'empty_name': [],
        'no_skills': [],
        'zero_score': [],
        'default_50_score': [],
        'no_email': [],
        'html_in_data': [],
        'garbled_skills': [],
        'duplicate_emails': [],
        'phone_issues': [],
    }
    
    for row in cursor.fetchall():
        c = dict(zip(columns, row))
        cid = c['id']
        name = (c.get('name') or '').strip()
        email = (c.get('email') or '').strip()
        summary = c.get('summary') or ''
        resume_text = c.get('resume_text') or ''
        skills_raw = c.get('skills') or '[]'
        score = c.get('match_score') or 0
        phone = c.get('phone') or ''
        
        try:
            skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
        except:
            skills = []
        
        # Gibberish check (full)
        is_gib, reason = is_gibberish_profile(c)
        if is_gib:
            issues['gibberish'].append({'id': cid, 'name': name[:40], 'email': email, 'reason': reason})
            continue  # Don't double-count other issues
        
        # Mojibake in name
        if name and is_mojibake(name):
            issues['mojibake_name'].append({'id': cid, 'name': name[:40], 'email': email})
        
        # Mojibake in text fields
        if (summary and is_mojibake(summary)) or (resume_text and is_mojibake(resume_text)):
            issues['mojibake_text'].append({'id': cid, 'name': name[:40], 'field': 'summary' if is_mojibake(summary) else 'resume'})
        
        # Empty name (but has data — fixable)
        if not name or name.lower() in ('unknown', 'n/a', '-'):
            issues['empty_name'].append({'id': cid, 'name': name, 'email': email})
        
        # No skills
        if not skills or (len(skills) == 1 and skills[0] in ('', 'N/A', 'None')):
            issues['no_skills'].append({'id': cid, 'name': name[:40], 'email': email})
        
        # Zero score
        if score == 0 or score is None:
            issues['zero_score'].append({'id': cid, 'name': name[:40]})
        elif score == 50:
            issues['default_50_score'].append({'id': cid, 'name': name[:40]})
        
        # No email
        if not email or '@' not in email:
            issues['no_email'].append({'id': cid, 'name': name[:40], 'email': email})
        
        # HTML in data
        if HTML_TAG_RE.search(name + summary[:200]):
            issues['html_in_data'].append({'id': cid, 'name': name[:40]})
        
        # Garbled skills
        for sk in skills:
            if is_mojibake(str(sk)):
                issues['garbled_skills'].append({'id': cid, 'name': name[:40], 'skill': str(sk)[:30]})
                break
        
        # Phone issues (looks like a year)
        if phone and re.match(r'^(19|20)\d{2}$', phone.strip()):
            issues['phone_issues'].append({'id': cid, 'name': name[:40], 'phone': phone})
    
    # Duplicate emails
    cursor.execute("""
        SELECT email, COUNT(*) as cnt FROM candidates 
        WHERE (is_active = 1 OR is_active IS NULL) 
        GROUP BY email HAVING cnt > 1
    """)
    for row in cursor.fetchall():
        issues['duplicate_emails'].append({'email': row[0], 'count': row[1]})
    
    report['issues'] = {k: v for k, v in issues.items() if v}
    report['issue_counts'] = {k: len(v) for k, v in issues.items()}
    report['total_issues'] = sum(len(v) for v in issues.values())
    
    # Add sample problematic candidates
    cursor.execute("""
        SELECT id, name, email, match_score, job_category,
               substr(summary, 1, 100) as summary_preview
        FROM candidates
        WHERE (is_active = 1 OR is_active IS NULL)
        AND (match_score = 0 OR match_score IS NULL 
             OR name LIKE '%Ã%' OR name LIKE '%â€%' 
             OR summary LIKE '%Ã%Ã%Ã%' OR name = '' OR name = 'Unknown')
        LIMIT 15
    """)
    cols = [d[0] for d in cursor.description]
    report['samples'] = [dict(zip(cols, r)) for r in cursor.fetchall()]
    
    return report


def repair_database(conn, scraper_service=None, ai_service=None) -> Dict[str, Any]:
    """
    Comprehensive database repair:
    1. Delete truly gibberish profiles (system emails, chat transcripts, etc.)
    2. Fix mojibake/encoding in names, summaries, resume text
    3. Extract real names from text for empty/garbled names
    4. Clean HTML from text fields
    5. Fix garbled skills
    6. Fix phone-as-year issues
    7. Deduplicate by email (keep best profile)
    8. Mark zero-score candidates for re-scoring
    
    Returns detailed results dict.
    """
    cursor = conn.cursor()
    # Ensure writes are durable (critical for Cloud Run ephemeral filesystems)
    try:
        cursor.execute("PRAGMA synchronous=FULL")
    except Exception:
        pass
    now = datetime.now().isoformat()
    
    results = {
        'deleted': [],
        'encoding_fixed': [],
        'names_recovered': [],
        'html_cleaned': [],
        'skills_fixed': [],
        'skills_extracted': [],
        'phones_fixed': [],
        'duplicates_merged': [],
        'rescored': [],
        'needs_rescore': [],
        'errors': [],
    }
    
    # Fetch all candidates
    cursor.execute("""
        SELECT id, email, name, phone, location, skills, experience,
               summary, resume_text, raw_email_subject, match_score,
               job_category, job_subcategory, status, education, work_history,
               certifications, languages, strengths, gaps
        FROM candidates
    """)
    columns = [d[0] for d in cursor.description]
    all_rows = cursor.fetchall()
    
    logger.info(f"DB Repair: scanning {len(all_rows)} candidates...")
    
    for row in all_rows:
        c = dict(zip(columns, row))
        cid = c['id']
        name = (c.get('name') or '').strip()
        email = (c.get('email') or '').lower().strip()
        summary = c.get('summary') or ''
        resume_text = c.get('resume_text') or ''
        skills_raw = c.get('skills') or '[]'
        phone = c.get('phone') or ''
        score = c.get('match_score') or 0
        
        try:
            skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
        except:
            skills = []
        
        updates = {}  # column -> new_value
        
        # ─── PHASE 1: Delete gibberish profiles ─────────────────────────
        is_gib, reason = is_gibberish_profile(c)
        if is_gib:
            # Before deleting, try to salvage useful data
            combined_text = (summary + ' ' + resume_text).strip()
            salvaged_name = extract_name_from_text(combined_text)
            salvaged_email = extract_email_from_text(combined_text)
            
            if salvaged_name and salvaged_email and salvaged_email != email:
                # There's a real candidate buried in this gibberish
                new_id = hashlib.md5(salvaged_email.encode()).hexdigest()
                cursor.execute("SELECT id FROM candidates WHERE id = ?", (new_id,))
                if cursor.fetchone():
                    # Real candidate already exists, just delete gibberish
                    cursor.execute("DELETE FROM candidates WHERE id = ?", (cid,))
                    results['deleted'].append({
                        'id': cid, 'name': name[:40], 'email': email,
                        'reason': reason, 'note': 'real candidate exists'
                    })
                else:
                    # Salvage: convert this gibberish record into the real candidate
                    msg_start = re.search(r'(?:dear|hi\s*,|hello|i\s+am|my\s+name)', combined_text, re.I)
                    clean_summary = combined_text[msg_start.start():msg_start.start()+1500] if msg_start else summary[:1000]
                    cursor.execute("""
                        UPDATE candidates SET id=?, email=?, name=?, summary=?, last_updated=?
                        WHERE id = ?
                    """, (new_id, salvaged_email, salvaged_name, clean_summary.strip(), now, cid))
                    results['names_recovered'].append({
                        'old_id': cid, 'new_id': new_id,
                        'old_name': name[:40], 'new_name': salvaged_name,
                        'reason': f'salvaged from {reason}'
                    })
                continue
            else:
                cursor.execute("DELETE FROM candidates WHERE id = ?", (cid,))
                results['deleted'].append({
                    'id': cid, 'name': name[:40], 'email': email, 'reason': reason
                })
                continue
        
        # ─── PHASE 2: Fix encoding (mojibake) ───────────────────────────
        # (intermediate commit after Phase 1 deletes, done once after loop)
        encoding_changed = False
        
        if name and is_mojibake(name):
            fixed = try_fix_encoding(name)
            if fixed != name and not is_mojibake(fixed):
                updates['name'] = fixed
                encoding_changed = True
                results['encoding_fixed'].append({
                    'id': cid, 'field': 'name',
                    'old': name[:40], 'new': fixed[:40]
                })
            else:
                # Try extracting name from other text
                recovered = extract_name_from_text(summary + ' ' + resume_text)
                if recovered:
                    updates['name'] = recovered
                    results['names_recovered'].append({
                        'id': cid, 'old_name': name[:40], 'new_name': recovered,
                        'reason': 'extracted from text (mojibake name unfixable)'
                    })
        
        if summary and is_mojibake(summary):
            fixed = try_fix_encoding(summary)
            if fixed != summary and not is_mojibake(fixed):
                updates['summary'] = fixed
                encoding_changed = True
        
        if resume_text and is_mojibake(resume_text):
            fixed = try_fix_encoding(resume_text)
            if fixed != resume_text and not is_mojibake(fixed):
                updates['resume_text'] = fixed
                encoding_changed = True
        
        if encoding_changed and 'name' not in [r.get('field') for r in results['encoding_fixed'] if r['id'] == cid]:
            results['encoding_fixed'].append({
                'id': cid, 'name': name[:40], 'field': 'text_fields'
            })
        
        # ─── PHASE 3: Fix empty/bad names ────────────────────────────────
        current_name = updates.get('name', name)
        if not current_name or current_name.lower() in ('unknown', 'n/a', '-', 'null', 'none', ''):
            recovered = extract_name_from_text(summary + ' ' + resume_text)
            if recovered:
                updates['name'] = recovered
                results['names_recovered'].append({
                    'id': cid, 'old_name': current_name or '(empty)',
                    'new_name': recovered, 'reason': 'empty_name_recovered'
                })
            elif email and '@' in email:
                # Use email local part as fallback name
                local = email.split('@')[0]
                # Convert john.doe or john_doe to "John Doe"
                name_from_email = ' '.join(
                    part.capitalize() for part in re.split(r'[._\-]', local)
                    if part and not part.isdigit()
                )
                if len(name_from_email) > 3:
                    updates['name'] = name_from_email
                    results['names_recovered'].append({
                        'id': cid, 'old_name': current_name or '(empty)',
                        'new_name': name_from_email, 'reason': 'derived from email'
                    })
        
        # ─── PHASE 4: Clean HTML from text fields ───────────────────────
        for field in ('summary', 'resume_text'):
            val = updates.get(field, c.get(field) or '')
            if val and HTML_TAG_RE.search(val):
                cleaned = clean_html_from_text(val)
                if cleaned != val:
                    updates[field] = cleaned
                    results['html_cleaned'].append({'id': cid, 'field': field})
        
        # ─── PHASE 5: Fix garbled skills ─────────────────────────────────
        fixed_skills = False
        clean_skills = []
        for sk in skills:
            sk_str = str(sk).strip()
            if is_mojibake(sk_str):
                fixed_sk = try_fix_encoding(sk_str)
                if fixed_sk != sk_str and not is_mojibake(fixed_sk):
                    clean_skills.append(fixed_sk)
                    fixed_skills = True
                # Skip unfixable garbled skills
            elif HTML_TAG_RE.search(sk_str) or HTML_ENTITY_RE.search(sk_str):
                cleaned = clean_html_from_text(sk_str)
                if cleaned and len(cleaned) > 1:
                    clean_skills.append(cleaned)
                    fixed_skills = True
            elif sk_str and sk_str not in ('N/A', 'None', '', '-'):
                clean_skills.append(sk_str)
        
        if fixed_skills:
            updates['skills'] = json.dumps(clean_skills)
            results['skills_fixed'].append({'id': cid, 'name': name[:40]})
        
        # ─── PHASE 6: Fix phone issues ──────────────────────────────────
        if phone:
            clean_phone = phone.strip()
            # Year masquerading as phone
            if re.match(r'^(19|20)\d{2}$', clean_phone):
                updates['phone'] = ''
                results['phones_fixed'].append({
                    'id': cid, 'old': clean_phone, 'reason': 'was_a_year'
                })
            # Too short (less than 7 digits)
            elif len(re.sub(r'\D', '', clean_phone)) < 7 and len(re.sub(r'\D', '', clean_phone)) > 0:
                updates['phone'] = ''
                results['phones_fixed'].append({
                    'id': cid, 'old': clean_phone, 'reason': 'too_short'
                })
        
        # ─── PHASE 7: Re-score candidates with default 50 or 0 ─────────
        if score == 50 or score == 0 or score is None:
            new_score = keyword_based_score(c)
            if new_score != score:
                updates['match_score'] = new_score
                results['rescored'].append({
                    'id': cid, 'name': (updates.get('name') or name)[:40],
                    'old_score': score, 'new_score': new_score
                })
        
        # ─── PHASE 8: Extract skills for candidates with none ────────────
        if not skills or (len(skills) == 1 and skills[0] in ('', 'N/A', 'None')):
            text_for_skills = (updates.get('summary', summary) + ' ' + updates.get('resume_text', resume_text)).strip()
            extracted = extract_skills_from_text(text_for_skills)
            if extracted:
                updates['skills'] = json.dumps(extracted)
                results['skills_extracted'].append({
                    'id': cid, 'name': (updates.get('name') or name)[:40],
                    'skills_found': len(extracted)
                })
        
        # ─── PHASE 9: Delete candidates with no email (system entries) ───
        if not email or '@' not in email:
            # Check if this is a system entry with no useful data
            text = (summary + ' ' + resume_text).strip()
            if len(text) < 50 and len(skills) < 2:
                cursor.execute("DELETE FROM candidates WHERE id = ?", (cid,))
                results['deleted'].append({
                    'id': cid, 'name': name[:40], 'email': email or '(none)',
                    'reason': 'no_email_no_data'
                })
                continue
        
        # ─── Apply updates ───────────────────────────────────────────────
        if updates:
            updates['last_updated'] = now
            set_parts = [f"{k} = ?" for k in updates]
            vals = list(updates.values()) + [cid]
            try:
                cursor.execute(
                    f"UPDATE candidates SET {', '.join(set_parts)} WHERE id = ?",
                    vals
                )
            except Exception as e:
                results['errors'].append({'id': cid, 'error': str(e)[:100]})
    
    # Commit all per-candidate changes before dedup phase
    conn.commit()
    logger.info(f"DB Repair: committed per-candidate fixes ({len(results['deleted'])} deleted, {len(results['encoding_fixed'])} encoding, {len(results['names_recovered'])} names)")
    
    # ─── PHASE 8: Deduplicate by email ───────────────────────────────────
    cursor.execute("""
        SELECT email, GROUP_CONCAT(id) as ids, COUNT(*) as cnt
        FROM candidates
        WHERE (is_active = 1 OR is_active IS NULL) AND email != '' AND email IS NOT NULL
        GROUP BY email HAVING cnt > 1
    """)
    for dup_row in cursor.fetchall():
        dup_email = dup_row[0]
        dup_ids = dup_row[1].split(',')
        
        # Find the "best" profile: highest score, most skills, newest
        best_id = None
        best_score = -1
        for did in dup_ids:
            cursor.execute("""
                SELECT id, match_score, skills, summary, resume_text, last_updated
                FROM candidates WHERE id = ?
            """, (did,))
            d = cursor.fetchone()
            if d:
                d_score = d[1] or 0
                d_skills = len(json.loads(d[2] or '[]'))
                d_text = len(d[3] or '') + len(d[4] or '')
                composite = d_score * 10 + d_skills * 5 + d_text
                if composite > best_score:
                    best_score = composite
                    best_id = d[0]
        
        if best_id:
            to_delete = [did for did in dup_ids if did != best_id]
            for del_id in to_delete:
                cursor.execute("DELETE FROM candidates WHERE id = ?", (del_id,))
            results['duplicates_merged'].append({
                'email': dup_email, 'kept': best_id, 'deleted': to_delete
            })
    
    conn.commit()
    
    # Summary
    summary_counts = {
        'deleted': len(results['deleted']),
        'encoding_fixed': len(results['encoding_fixed']),
        'names_recovered': len(results['names_recovered']),
        'html_cleaned': len(results['html_cleaned']),
        'skills_fixed': len(results['skills_fixed']),
        'skills_extracted': len(results['skills_extracted']),
        'phones_fixed': len(results['phones_fixed']),
        'duplicates_merged': len(results['duplicates_merged']),
        'rescored': len(results['rescored']),
        'needs_rescore': len(results['needs_rescore']),
        'errors': len(results['errors']),
    }
    results['summary'] = summary_counts
    results['total_fixed'] = sum(v for k, v in summary_counts.items() if k not in ('needs_rescore', 'errors'))
    
    logger.info(
        f"DB Repair complete: {results['total_fixed']} fixed, "
        f"{summary_counts['deleted']} deleted, {summary_counts['needs_rescore']} need rescore"
    )
    
    return results


def quick_health_check(conn) -> Dict[str, Any]:
    """
    Fast health check for startup — returns counts of issues without full scan details.
    Used to decide if a full repair should be triggered.
    """
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM candidates WHERE is_active = 1 OR is_active IS NULL')
    total = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM candidates 
        WHERE (is_active = 1 OR is_active IS NULL) 
        AND (match_score = 0 OR match_score IS NULL)
    """)
    zero_score = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM candidates
        WHERE (is_active = 1 OR is_active IS NULL)
        AND (name LIKE '%Ã%' OR name LIKE '%â€%' OR name = '' OR name = 'Unknown' OR name IS NULL)
    """)
    bad_names = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM candidates
        WHERE (is_active = 1 OR is_active IS NULL)
        AND (summary LIKE '%Ã%Ã%Ã%' OR resume_text LIKE '%Ã%Ã%Ã%')
    """)
    mojibake_text = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM candidates
        WHERE (is_active = 1 OR is_active IS NULL)  
        AND (email LIKE '%noreply%' OR email LIKE '%@zohosalesiq%' 
             OR email LIKE '%@uploaded.local' OR email LIKE '%systemgenerated%'
             OR email LIKE '%@indeedemail.com')
    """)
    system_emails = cursor.fetchone()[0]
    
    has_issues = zero_score > 0 or bad_names > 0 or mojibake_text > 0 or system_emails > 0
    
    return {
        'total_candidates': total,
        'zero_score': zero_score,
        'bad_names': bad_names,
        'mojibake_text': mojibake_text,
        'system_emails': system_emails,
        'needs_repair': has_issues,
        'issue_count': zero_score + bad_names + mojibake_text + system_emails,
    }

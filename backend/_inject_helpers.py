"""Inject inter-route helper code into modular route files."""
import re

with open('main_legacy.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

def get_lines(start_0idx, end_0idx):
    """Get lines by 0-indexed range."""
    return ''.join(lines[start_0idx:end_0idx])

def do_replacements(code):
    """Apply same service replacements as extraction script."""
    code = re.sub(r'\bdb_service\.', '_db().', code)
    code = re.sub(r'\bai_service\.', '_ai().', code)
    code = re.sub(r'\bgemini_service\.', '_gemini().', code)
    code = re.sub(r'\bscraper_service\.', '_scraper().', code)
    code = re.sub(r'\bresume_parser\.', '_resume_parser().', code)
    code = re.sub(r'\bemail_parser\.', '_email_parser().', code)
    code = re.sub(r'\bmatching_engine\.', '_matching_engine().', code)
    code = re.sub(r'\bresponse_cache\.', '_cache().', code)
    code = re.sub(r'\bAI_ANALYSIS_TIMEOUT\b', '_deps().AI_ANALYSIS_TIMEOUT', code)
    code = re.sub(r'\bAI_TIMEOUT\b', '_deps().AI_TIMEOUT', code)
    return code

# ── 1. candidates_routes.py: CANONICAL_CATEGORIES + helpers ──────────────
# Lines 3511-3652 (0-indexed: 3510-3651)
canonical_code = do_replacements(get_lines(3510, 3652))

with open('api/candidates_routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

insert_before = '@router.post("/api/candidates/normalize-categories")'
if canonical_code not in content and insert_before in content:
    content = content.replace(
        insert_before,
        '# ── Category constants (extracted from main_legacy.py) ──\n' + canonical_code + '\n\n' + insert_before
    )
    with open('api/candidates_routes.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    print('Injected CANONICAL_CATEGORIES into candidates_routes.py')
else:
    print('candidates_routes.py: already present or anchor not found')

# ── 2. email_routes.py: process_single_email() ───────────────────────────
# Lines 7326-7424 (0-indexed: 7325-7423)
process_email_code = do_replacements(get_lines(7325, 7424))

with open('api/email_routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

insert_before = '@router.post("/api/email/webhook")'
if 'process_single_email' not in content and insert_before in content:
    content = content.replace(
        insert_before,
        '# ── Webhook helper ──\n' + process_email_code + '\n\n' + insert_before
    )
    with open('api/email_routes.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    print('Injected process_single_email into email_routes.py')
else:
    print('email_routes.py: already present or anchor not found')

# ── 3. shortlist_routes.py: _send_rejection_email() + _send_shortlist_email() ──
# Lines 8367-8634 (0-indexed: 8366-8634)
shortlist_helpers = do_replacements(get_lines(8366, 8634))

# Add needed imports to shortlist_routes.py
with open('api/shortlist_routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add imports for MicrosoftGraphService and token_storage
extra_imports = (
    'from services.microsoft_graph import MicrosoftGraphService\n'
    'from services.token_storage import get_token_storage\n'
)
insert_after_deps = 'from core.dependencies import require_auth, optional_auth, require_admin'
if 'MicrosoftGraphService' not in content:
    content = content.replace(
        insert_after_deps,
        insert_after_deps + '\n' + extra_imports
    )

# Inject helpers before the status route
insert_before = '@router.put("/api/candidates/{candidate_id}/status")'
if '_send_rejection_email' not in content and insert_before in content:
    content = content.replace(
        insert_before,
        '# ── Shortlist email helpers ──\n' + shortlist_helpers + '\n\n' + insert_before
    )
    with open('api/shortlist_routes.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    print('Injected email helpers into shortlist_routes.py')
else:
    with open('api/shortlist_routes.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    print('shortlist_routes.py: helpers already present or anchor not found')

# ── 4. ai_routes.py: _quick_fallback_analysis() + _format_search_results() ──
# _quick_fallback_analysis: Lines 9518-9550 (0-indexed: 9517-9550)
fallback_code = do_replacements(get_lines(9517, 9551))
# _format_search_results: Lines 9845-9900 (0-indexed: 9844-9900)
format_code = do_replacements(get_lines(9844, 9901))

with open('api/ai_routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Inject _quick_fallback_analysis before analyze-match
insert_before_analyze = '@router.post("/api/ai/analyze-match")'
if '_quick_fallback_analysis' not in content and insert_before_analyze in content:
    content = content.replace(
        insert_before_analyze,
        '# ── Rule-based fallback ──\n' + fallback_code + '\n\n' + insert_before_analyze
    )
    print('Injected _quick_fallback_analysis into ai_routes.py')
else:
    print('ai_routes.py: _quick_fallback_analysis already present or anchor not found')

# Inject _format_search_results before smart-search
insert_before_search = '@router.post("/api/ai/smart-search")'
if '_format_search_results' not in content and insert_before_search in content:
    content = content.replace(
        insert_before_search,
        '# ── Search result formatter ──\n' + format_code + '\n\n' + insert_before_search
    )
    print('Injected _format_search_results into ai_routes.py')
else:
    print('ai_routes.py: _format_search_results already present or anchor not found')

with open('api/ai_routes.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print('\nDone injecting helpers!')

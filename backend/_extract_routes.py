"""
Extract routes from main_legacy.py into modular route files.
Each route file uses APIRouter and imports services from api.deps.
"""
import re
import os

with open('main_legacy.py', 'r', encoding='utf-8') as f:
    all_lines = f.readlines()


def find_route_blocks():
    blocks = []
    i = 0
    while i < len(all_lines):
        m = re.match(r'^@app\.(get|post|put|patch|delete)\(["\']([^"\']*)["\']([^)]*)\)', all_lines[i])
        if m:
            method = m.group(1)
            path = m.group(2)
            extra = m.group(3).strip()
            block_start = i
            j = i + 1
            while j < len(all_lines) and not (all_lines[j].startswith('async def ') or all_lines[j].startswith('def ')):
                j += 1
            if j >= len(all_lines):
                i += 1
                continue
            func_indent = len(all_lines[j]) - len(all_lines[j].lstrip())
            # If the async def line opens parentheses that don't close on the same line,
            # scan forward to find the closing '):\n' at indent 0.
            # This handles multi-line function signatures.
            sig_end = j
            open_def_line = all_lines[j]
            # Check if function signature is multi-line: parens don't balance on def line
            def_opens = open_def_line.count('(')
            def_closes = open_def_line.count(')')
            if def_opens > def_closes:
                # Find closing '):\n' line
                sig_end = j + 1
                while sig_end < len(all_lines):
                    stripped = all_lines[sig_end].rstrip()
                    if stripped in ('):', ) or stripped.endswith('):') and len(stripped) <= 3:
                        sig_end += 1  # body starts after the '):\n' line
                        break
                    sig_end += 1
            else:
                sig_end = j + 1
            j = sig_end
            while j < len(all_lines):
                line = all_lines[j]
                if line.strip() == '':
                    j += 1
                    continue
                cur_indent = len(line) - len(line.lstrip())
                if cur_indent <= func_indent and line.strip() != '':
                    break
                j += 1
            blocks.append((block_start, j, method, path, extra))
            i = j
        else:
            i += 1
    return blocks


blocks = find_route_blocks()


def classify_route(path):
    overrides = {
        '/api/candidates/{candidate_id}/status': 'shortlist',
        '/api/candidates/bulk-shortlist': 'shortlist',
        '/api/candidates/reset-shortlist': 'shortlist',
        '/api/candidates/{candidate_id}/rescore': 'ai',
        '/api/candidates/{candidate_id}/ai-analysis': 'ai',
        '/api/email/test-send': 'shortlist',
        '/api/ai/generate-shortlist-email': 'shortlist',
        '/api/auth/auto-authenticate': 'email',
    }
    if path in overrides:
        return overrides[path]
    prefix_map = [
        ('/api/admin/', 'admin'),
        ('/api/setup/', 'settings'),
        ('/api/scraper/', 'settings'),
        ('/api/stats/', 'settings'),
        ('/api/search-history', 'settings'),
        ('/api/llm/status', 'settings'),
        ('/api/candidates/', 'candidates'),
        ('/api/email/', 'email'),
        ('/api/oauth/', 'email'),
        ('/api/oauth2/', 'email'),
        ('/api/cron/', 'email'),
        ('/api/ai/', 'ai'),
        ('/api/resumes/', 'upload'),
        ('/api/jd/', 'job'),
        ('/api/taxonomy/', 'job'),
        ('/api/matching/', 'job'),
        ('/api/audit/', 'shortlist'),
        ('/api/auth/login', 'auth'),
        ('/api/auth/register', 'auth'),
        ('/api/auth/me', 'auth'),
        ('/api/users/', 'auth'),
    ]
    for prefix, module in prefix_map:
        if path == prefix.rstrip('/') or path.startswith(prefix):
            return module
    if path in ('/', '/version', '/health'):
        return 'settings'
    return None


# Group blocks by module
module_blocks = {}
for start, end, method, path, extra in blocks:
    module = classify_route(path)
    if module:
        module_blocks.setdefault(module, []).append((start, end, method, path, extra))


def convert_route_block(start, end, method, path, extra):
    """Convert a @app.method route block to use router."""
    block_lines = list(all_lines[start:end])

    # Replace @app.method with @router.method
    extra_str = extra.strip(', ') if extra else ''
    new_first = '@router.' + method + '("' + path + '"'
    if extra_str:
        new_first += ', ' + extra_str
    new_first += ')\n'
    block_lines[0] = new_first

    return ''.join(block_lines)


HEADER_TEMPLATE = '''"""Route module: {module}. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import logging
import re
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(tags=["{module}"])


# ---- Service accessors (lazy imports to avoid circular deps) ----

def _db():
    from api.deps import get_db
    return get_db()

def _ai():
    from api.deps import get_ai
    return get_ai()

def _gemini():
    from api.deps import get_gemini
    return get_gemini()

def _scraper():
    from api.deps import get_scraper
    return get_scraper()

def _resume_parser():
    from api.deps import get_resume_parser
    return get_resume_parser()

def _email_parser():
    from api.deps import get_email_parser
    return get_email_parser()

def _auth_svc():
    from api.deps import get_auth
    return get_auth()

def _matching_engine():
    from api.deps import get_matching_engine
    return get_matching_engine()

def _cache():
    from api.deps import response_cache
    return response_cache

def _get_cache_lock():
    from api.deps import _cache_lock
    return _cache_lock

def _deps():
    """Get deps module for constants."""
    import api.deps as deps
    return deps


'''


def do_service_replacements(content):
    """Replace legacy global service references with accessor calls."""
    content = re.sub(r'\bdb_service\.', '_db().', content)
    content = re.sub(r'\bai_service\.', '_ai().', content)
    content = re.sub(r'\bgemini_service\.', '_gemini().', content)
    content = re.sub(r'\bgemini_service\b(?!\.)', '_gemini()', content)
    content = re.sub(r'\bscraper_service\.', '_scraper().', content)
    content = re.sub(r'\bresume_parser\.', '_resume_parser().', content)
    content = re.sub(r'\bemail_parser\.', '_email_parser().', content)
    content = re.sub(r'\bmatching_engine\.', '_matching_engine().', content)
    content = re.sub(r'\bresponse_cache\.', '_cache().', content)
    content = re.sub(r'\blen\(response_cache\)', 'len(_cache())', content)
    content = re.sub(r'\bMAX_CONCURRENT_REQUESTS\b', '_deps().MAX_CONCURRENT_REQUESTS', content)
    content = re.sub(r'\bAI_TIMEOUT\b', '_deps().AI_TIMEOUT', content)
    content = re.sub(r'\bAI_ANALYSIS_TIMEOUT\b', '_deps().AI_ANALYSIS_TIMEOUT', content)
    # Replace _cache_lock used as context manager
    content = re.sub(r'\bwith _cache_lock:', 'with _get_cache_lock():', content)
    content = re.sub(r'\b_cache_lock\b', '_get_cache_lock()', content)
    # Fix double calls
    content = content.replace('_get_cache_lock()()', '_get_cache_lock()')
    return content


# Write each module
for module, blk_list in module_blocks.items():
    if module == 'auth':
        continue  # Already exists

    route_parts = []
    for start, end, method, path, extra in blk_list:
        code = convert_route_block(start, end, method, path, extra)
        route_parts.append(code)

    route_code = '\n\n'.join(route_parts)
    header = HEADER_TEMPLATE.format(module=module)
    full_content = header + route_code + '\n'
    full_content = do_service_replacements(full_content)

    filename = os.path.join('api', module + '_routes.py')
    with open(filename, 'w', encoding='utf-8', newline='\n') as f:
        f.write(full_content)

    print(f"Wrote {filename}: {len(blk_list)} routes, {len(full_content)} bytes")

print("\nDone! All route modules generated.")

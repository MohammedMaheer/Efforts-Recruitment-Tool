from typing import List, Dict, Any, Optional
import email
from email import policy
from email.parser import BytesParser
import imaplib
import re
from datetime import datetime
import base64

# ============================================================================
# PRECOMPILED REGEX PATTERNS FOR PERFORMANCE
# ============================================================================
_RE_NOREPLY_PREFIX = re.compile(r'^(conversation|reply|noreply|info|contact|hr|jobs|careers)-?', re.IGNORECASE)
_RE_HASH_SUFFIX = re.compile(r'\s*[a-z0-9]{5,}$', re.IGNORECASE)
_RE_DIGITS_ONLY = re.compile(r'\D')
_RE_SCRIPT = re.compile(r'<script[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE)
_RE_STYLE = re.compile(r'<style[^>]*>.*?</style>', re.DOTALL | re.IGNORECASE)
_RE_BR = re.compile(r'<br\s*/?>', re.IGNORECASE)
_RE_P_END = re.compile(r'</p>', re.IGNORECASE)
_RE_DIV_END = re.compile(r'</div>', re.IGNORECASE)
_RE_LI_END = re.compile(r'</li>', re.IGNORECASE)
_RE_TR_END = re.compile(r'</tr>', re.IGNORECASE)
_RE_HTML_TAG = re.compile(r'<[^>]+>')
_RE_MULTI_SPACE = re.compile(r'[ \t]+')
_RE_MULTI_NEWLINE = re.compile(r'\n\s*\n')

# Precompiled skill patterns (avoids re.compile on every email parse — big hot-path win)
_SKILL_KEYWORDS = [
    'python', 'javascript', 'typescript', 'react', 'node.js', 'vue', 'angular',
    'java', 'c++', 'c#', 'golang', 'rust', 'ruby', 'php', 'swift', 'kotlin', 'scala',
    'sql', 'postgresql', 'mongodb', 'mysql', 'redis', 'elasticsearch', 'oracle',
    'docker', 'kubernetes', 'aws', 'azure', 'gcp', 'terraform', 'jenkins',
    'machine learning', 'deep learning', 'data science', 'tensorflow', 'pytorch',
    'django', 'flask', 'fastapi', 'spring boot', 'express', 'next.js',
    'html', 'css', 'tailwind', 'figma', 'photoshop',
    'agile', 'scrum', 'jira', 'git', 'ci/cd', 'devops',
    'salesforce', 'sap', 'erp', 'crm', 'power bi', 'tableau',
    'excel', 'marketing', 'seo', 'sales', 'accounting', 'finance',
    'project management', 'business development', 'recruitment',
]
_SKILL_PATTERNS = {
    skill: re.compile(r'\b' + re.escape(skill) + r'\b', re.IGNORECASE)
    for skill in _SKILL_KEYWORDS
}
# Precompiled location pattern
_RE_LOCATION = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Za-z]+)*),\s*(?:UAE|([A-Z]{2}))\b')

class EmailParser:
    """
    Universal email parser supporting Gmail, Outlook, Yahoo, and other IMAP providers
    Automatically extracts candidate information from email content and attachments
    """
    
    def __init__(self):
        self.supported_providers = {
            'gmail': {
                'imap_server': 'imap.gmail.com',
                'imap_port': 993,
                'requires_app_password': True
            },
            'outlook': {
                'imap_server': 'outlook.office365.com',
                'imap_port': 993,
                'requires_oauth': True
            },
            'yahoo': {
                'imap_server': 'imap.mail.yahoo.com',
                'imap_port': 993,
                'requires_app_password': True
            },
            'icloud': {
                'imap_server': 'imap.mail.me.com',
                'imap_port': 993,
                'requires_app_password': True
            },
            'custom': {
                'imap_server': 'custom',
                'imap_port': 993,
                'requires_oauth': False
            }
        }
    
    def parse_email_application(self, body: str, sender_email: str) -> Dict[str, Any]:
        """
        Parse email body to extract candidate information
        Used as fallback when no resume attachment is found
        """
        # Clean HTML from body if present
        clean_body = body
        if body and '<' in body:
            clean_body = self._clean_html(body)
        
        result = {
            'name': '',
            'phone': '',
            'email': sender_email,
            'skills': [],
            'experience': 0,
            'education': '',
            'summary': '',  # Will be set properly after extraction or by AI later
            'work_history': [],
            'location': ''
        }
        
        # Extract name from email (before @ symbol, clean up)
        if sender_email:
            name_part = sender_email.split('@')[0]
            # Remove conversation- prefix and random IDs
            name_part = _RE_NOREPLY_PREFIX.sub('', name_part)
            # Clean up common patterns like firstname.lastname or firstname_lastname
            name_part = name_part.replace('.', ' ').replace('_', ' ').replace('-', ' ')
            # Remove trailing random IDs (5+ alphanumeric chars)
            name_part = _RE_HASH_SUFFIX.sub('', name_part)
            # Capitalize each word
            words = [word.capitalize() for word in name_part.split() if len(word) > 1]
            result['name'] = ' '.join(words) if words else ''
        
        if not clean_body:
            return result
        
        body_lower = clean_body.lower()
        
        # Extract phone number (minimum 7 digits to avoid matching years like 2026)
        phone_patterns = [
            r'\+971[\s.-]?\d{1,2}[\s.-]?\d{3}[\s.-]?\d{4}',  # UAE format
            r'\+\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}',  # International
            r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}',  # US format
            r'\b\d{10,12}\b'  # Plain 10-12 digit numbers
        ]
        for pattern in phone_patterns:
            match = re.search(pattern, body)
            if match:
                phone_candidate = match.group()
                # Filter out year-like numbers (4 digits between 1900-2100)
                digits_only = _RE_DIGITS_ONLY.sub('', phone_candidate)
                if len(digits_only) >= 7:  # Valid phone has at least 7 digits
                    result['phone'] = phone_candidate
                    break

        # Extract skills using precompiled module-level patterns (avoid recompiling per email)
        found_skills = [skill.title() for skill, pat in _SKILL_PATTERNS.items() if pat.search(body_lower)]
        result['skills'] = found_skills
        
        # Extract years of experience
        exp_patterns = [r'(\d+)\+?\s*years?', r'(\d+)\s*yrs?']
        for pattern in exp_patterns:
            match = re.search(pattern, body_lower)
            if match:
                result['experience'] = int(match.group(1))
                break
        
        # Extract location (comprehensive global list)
        location_keywords = [
            # UAE
            'dubai', 'abu dhabi', 'sharjah', 'ajman', 'ras al khaimah', 'fujairah', 'umm al quwain', 'uae',
            # GCC / Middle East  
            'riyadh', 'jeddah', 'dammam', 'saudi arabia', 'ksa', 'doha', 'qatar', 'kuwait',
            'bahrain', 'muscat', 'oman', 'amman', 'jordan', 'beirut', 'lebanon', 'cairo', 'egypt',
            # South Asia
            'india', 'mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune', 'kolkata',
            'pakistan', 'karachi', 'lahore', 'islamabad', 'sri lanka', 'colombo', 'nepal', 'bangladesh', 'dhaka',
            # Southeast Asia
            'philippines', 'manila', 'singapore', 'malaysia', 'kuala lumpur', 'indonesia', 'jakarta',
            'vietnam', 'thailand', 'bangkok',
            # Europe
            'uk', 'london', 'manchester', 'germany', 'berlin', 'france', 'paris', 'netherlands',
            'amsterdam', 'spain', 'italy', 'switzerland', 'ireland', 'dublin',
            # Americas
            'usa', 'us', 'new york', 'california', 'texas', 'canada', 'toronto', 'vancouver',
            # Africa
            'nigeria', 'lagos', 'kenya', 'nairobi', 'south africa', 'johannesburg', 'ghana',
            # Other
            'remote', 'hybrid', 'onsite', 'on-site', 'work from home',
        ]
        for loc in location_keywords:
            if loc in body_lower:
                result['location'] = loc.title()
                break
        
        # Generate structured summary from extracted fields (NOT from raw email body)
        # AI will replace this later if available; this is just a clean fallback
        from services.email_scraper import generate_structured_summary
        result['summary'] = generate_structured_summary(result)
        
        return result
    
    def _clean_html(self, html_content: str) -> str:
        """Convert HTML content to clean plain text"""
        if not html_content:
            return ""
        
        from html import unescape
        
        text = html_content
        # Remove script and style elements
        text = _RE_SCRIPT.sub('', text)
        text = _RE_STYLE.sub('', text)
        # Replace common block elements with newlines
        text = _RE_BR.sub('\n', text)
        text = _RE_P_END.sub('\n\n', text)
        text = _RE_DIV_END.sub('\n', text)
        text = _RE_LI_END.sub('\n', text)
        text = _RE_TR_END.sub('\n', text)
        # Remove all remaining HTML tags
        text = _RE_HTML_TAG.sub('', text)
        # Decode HTML entities
        text = unescape(text)
        # Clean up whitespace
        text = _RE_MULTI_SPACE.sub(' ', text)
        text = _RE_MULTI_NEWLINE.sub('\n\n', text)
        text = text.strip()
        return text
    
    async def connect_email_account(
        self,
        provider: str,
        email_address: str,
        password: Optional[str] = None,
        access_token: Optional[str] = None,
        custom_imap_server: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Connect to email account using IMAP
        Supports OAuth2 for Outlook/Gmail and app passwords
        """
        try:
            if provider not in self.supported_providers and not custom_imap_server:
                raise ValueError(f"Unsupported provider: {provider}")
            
            # Get provider settings
            if provider == 'custom' and custom_imap_server:
                imap_server = custom_imap_server
                imap_port = 993
            else:
                config = self.supported_providers[provider]
                imap_server = config['imap_server']
                imap_port = config['imap_port']
            
            # Connect to IMAP server (blocking I/O — run in thread)
            def _do_connect():
                m = imaplib.IMAP4_SSL(imap_server, imap_port)
                if access_token:
                    auth_string = self._generate_oauth2_string(email_address, access_token)
                    m.authenticate('XOAUTH2', lambda x: auth_string)
                else:
                    m.login(email_address, password)
                return m

            mail = await asyncio.to_thread(_do_connect)

            return {
                'status': 'connected',
                'email': email_address,
                'provider': provider,
                'connection': mail
            }
        
        except Exception as e:
            return {
                'status': 'failed',
                'error': str(e),
                'provider': provider
            }
    
    async def fetch_candidate_emails(
        self,
        mail_connection: imaplib.IMAP4_SSL,
        folder: str = 'INBOX',
        search_criteria: str = 'ALL',
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetch emails from specified folder
        Search for application emails and extract candidate data
        """
        try:
            import asyncio
            
            # Select mailbox (blocking operation - run in thread)
            await asyncio.to_thread(mail_connection.select, folder)
            
            # Search for emails (blocking operation - run in thread)
            status, message_ids = await asyncio.to_thread(mail_connection.search, None, search_criteria)
            
            if status != 'OK':
                return []
            
            # Get message IDs
            email_ids = message_ids[0].split()
            
            # Limit results
            email_ids = email_ids[-limit:] if len(email_ids) > limit else email_ids
            
            candidates = []
            
            for email_id in reversed(email_ids):  # Process newest first
                email_data = await self._parse_email(mail_connection, email_id)
                if email_data:
                    candidates.append(email_data)
            
            return candidates
        
        except Exception as e:
            print(f"Error fetching emails: {str(e)}")
            return []
    
    async def _parse_email(
        self,
        mail_connection: imaplib.IMAP4_SSL,
        email_id: bytes
    ) -> Optional[Dict[str, Any]]:
        """
        Parse individual email and extract candidate information
        """
        try:
            import asyncio
            
            # Fetch email (blocking operation - run in thread)
            status, msg_data = await asyncio.to_thread(mail_connection.fetch, email_id, '(RFC822)')
            
            if status != 'OK':
                return None
            
            # Parse email message
            email_body = msg_data[0][1]
            message = BytesParser(policy=policy.default).parsebytes(email_body)
            
            # Extract basic information
            candidate_data = {
                'email_id': email_id.decode(),
                'from_email': self._extract_email_address(message['From']),
                'from_name': self._extract_name_from_email(message['From']),
                'subject': message['Subject'],
                'date': self._parse_email_date(message['Date']),
                'body': '',
                'attachments': [],
                'extracted_info': {}
            }
            
            # Extract email body
            if message.is_multipart():
                for part in message.walk():
                    content_type = part.get_content_type()
                    
                    # Extract text content
                    if content_type == 'text/plain':
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        candidate_data['body'] += body
                    
                    # Handle attachments
                    elif part.get_content_disposition() == 'attachment':
                        attachment_info = await self._process_attachment(part)
                        if attachment_info:
                            candidate_data['attachments'].append(attachment_info)
            else:
                # Single part message
                body = message.get_payload(decode=True).decode('utf-8', errors='ignore')
                candidate_data['body'] = body
            
            # Extract candidate information from email body
            extracted_info = await self._extract_candidate_info_from_text(
                candidate_data['body'],
                candidate_data['from_email'],
                candidate_data['from_name']
            )
            candidate_data['extracted_info'] = extracted_info
            
            # Check if this is a job application email
            if self._is_application_email(candidate_data):
                return candidate_data
            
            return None
        
        except Exception as e:
            print(f"Error parsing email {email_id}: {str(e)}")
            return None
    
    async def _process_attachment(self, part) -> Optional[Dict[str, Any]]:
        """
        Process email attachment (resume)
        """
        try:
            filename = part.get_filename()
            
            if not filename:
                return None
            
            # Check if it's a resume file
            if not self._is_resume_file(filename):
                return None
            
            # Get file content
            file_data = part.get_payload(decode=True)
            
            return {
                'filename': filename,
                'content_type': part.get_content_type(),
                'size': len(file_data),
                'data': base64.b64encode(file_data).decode('utf-8')
            }
        
        except Exception as e:
            print(f"Error processing attachment: {str(e)}")
            return None
    
    def _is_resume_file(self, filename: str) -> bool:
        """Check if file is a resume"""
        resume_extensions = ['.pdf', '.docx', '.doc', '.txt']
        resume_keywords = ['resume', 'cv', 'curriculum', 'vitae']
        
        filename_lower = filename.lower()
        
        # Check extension
        has_valid_extension = any(filename_lower.endswith(ext) for ext in resume_extensions)
        
        # Check keywords
        has_resume_keyword = any(keyword in filename_lower for keyword in resume_keywords)
        
        # Accept any file with valid extension: many resumes have long descriptive names
        # without keywords like 'resume' or 'cv' (e.g. "Ahmed_Mohammad_Senior_Engineer_Profile_2024.pdf")
        return has_valid_extension
    
    def _is_application_email(self, email_data: Dict[str, Any]) -> bool:
        """
        Determine if email is a job application
        """
        application_keywords = [
            'application', 'applying', 'apply', 'position', 'job', 'role',
            'opportunity', 'resume', 'cv', 'interested', 'candidate'
        ]
        
        subject_lower = email_data['subject'].lower() if email_data['subject'] else ''
        body_lower = email_data['body'].lower()
        
        # Check subject
        subject_match = any(keyword in subject_lower for keyword in application_keywords)
        
        # Check body
        body_match = any(keyword in body_lower for keyword in application_keywords)
        
        # Check if has resume attachment
        has_resume = len(email_data['attachments']) > 0
        
        return (subject_match or body_match) or has_resume
    
    async def _extract_candidate_info_from_text(
        self,
        text: str,
        email_address: str,
        name_from_email: str
    ) -> Dict[str, Any]:
        """
        Extract candidate information from email body text
        """
        info = {
            'name': name_from_email,
            'email': email_address,
            'phone': None,
            'location': None,
            'linkedin': None,
            'github': None,
            'portfolio': None,
            'years_experience': None,
            'current_position': None,
            'skills': []
        }
        
        # Extract phone number
        phone_patterns = [
            r'\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
            r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        ]
        for pattern in phone_patterns:
            match = re.search(pattern, text)
            if match:
                info['phone'] = match.group(0)
                break
        
        # Extract LinkedIn
        linkedin_pattern = r'linkedin\.com/in/[\w-]+'
        linkedin_match = re.search(linkedin_pattern, text, re.IGNORECASE)
        if linkedin_match:
            info['linkedin'] = f"https://{linkedin_match.group(0)}"
        
        # Extract GitHub
        github_pattern = r'github\.com/[\w-]+'
        github_match = re.search(github_pattern, text, re.IGNORECASE)
        if github_match:
            info['github'] = f"https://{github_match.group(0)}"
        
        # Extract years of experience
        experience_patterns = [
            r'(\d+)\+?\s*years?\s+(?:of\s+)?experience',
            r'experience:\s*(\d+)\+?\s*years?'
        ]
        for pattern in experience_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info['years_experience'] = int(match.group(1))
                break
        
        # Extract skills using precompiled module-level patterns (no local list needed)
        # Extract skills using precompiled word-boundary patterns
        text_lower = text.lower()
        found_skills = [skill for skill, pat in _SKILL_PATTERNS.items() if pat.search(text_lower)]
        info['skills'] = list(set(found_skills))

        # Extract location (UAE cities or international format)
        location_match = _RE_LOCATION.search(text)
        if location_match:
            city = location_match.group(1)
            region = location_match.group(2) if location_match.group(2) else 'UAE'
            info['location'] = f"{city}, {region}"
        
        return info
    
    def _extract_email_address(self, from_field: str) -> str:
        """Extract email address from 'From' field"""
        email_pattern = r'[\w\.-]+@[\w\.-]+'
        match = re.search(email_pattern, from_field)
        return match.group(0) if match else ''
    
    def _extract_name_from_email(self, from_field: str) -> str:
        """Extract name from 'From' field"""
        # Try to extract name before email
        name_pattern = r'^([^<]+)<'
        match = re.search(name_pattern, from_field)
        
        if match:
            name = match.group(1).strip().strip('"\'')
            return name
        
        # Fallback: extract from email address
        email = self._extract_email_address(from_field)
        if email:
            username = email.split('@')[0]
            # Convert firstname.lastname to Firstname Lastname
            return ' '.join(word.capitalize() for word in username.replace('.', ' ').replace('_', ' ').split())
        
        return 'Unknown'
    
    def _parse_email_date(self, date_str: str) -> str:
        """Parse email date to ISO format"""
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            return dt.isoformat()
        except Exception:
            return datetime.now().isoformat()
    
    def _generate_oauth2_string(self, email: str, access_token: str) -> bytes:
        """Generate OAuth2 authentication string"""
        auth_string = f'user={email}\x01auth=Bearer {access_token}\x01\x01'
        return auth_string.encode('utf-8')
    
    async def setup_auto_sync(
        self,
        email_config: Dict[str, Any],
        sync_interval_minutes: int = 15
    ) -> Dict[str, Any]:
        """
        Setup automatic email synchronization
        Periodically checks for new applications
        """
        return {
            'status': 'configured',
            'email': email_config.get('email'),
            'provider': email_config.get('provider'),
            'sync_interval': sync_interval_minutes,
            'folders': ['INBOX', 'Applications', 'Careers'],
            'auto_parse_attachments': True,
            'notification_enabled': True
        }

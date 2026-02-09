"""
Multi-Email Account Scraper Service
Supports multiple email services simultaneously with full history processing
Now with Indeed, LinkedIn, and other job portal email parsing
"""
import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import imaplib
import email
from email.header import decode_header
import os
from dotenv import load_dotenv
import json
from html import unescape

from services.resume_parser import ResumeParser
from services.email_parser import EmailParser
# NOTE: OpenAI service removed - using local keyword matching for job categorization (zero API cost)


def clean_html_to_text(html_content: str) -> str:
    """Convert HTML content to clean plain text"""
    if not html_content:
        return ""
    
    # Remove script and style elements
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Replace common block elements with newlines
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
    
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Decode HTML entities
    text = unescape(text)
    
    # Clean up whitespace
    text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces to single
    text = re.sub(r'\n\s*\n', '\n\n', text)  # Multiple newlines to double
    text = text.strip()
    
    return text


def extract_name_from_email_address(email_address: str) -> str:
    """Extract a readable name from an email address"""
    if not email_address:
        return "Unknown"
    
    # Get the part before @
    name_part = email_address.split('@')[0]
    
    # Remove common prefixes like 'conversation-'
    name_part = re.sub(r'^(conversation|reply|noreply|info|contact|hr|jobs|careers)-?', '', name_part, flags=re.IGNORECASE)
    
    # Clean up common patterns
    name_part = name_part.replace('.', ' ').replace('_', ' ').replace('-', ' ')
    
    # Remove random strings (like IDs): sequences of more than 4 mixed chars/digits at the end
    name_part = re.sub(r'[a-z0-9]{5,}$', '', name_part, flags=re.IGNORECASE)
    
    # Remove trailing numbers/random chars
    name_part = re.sub(r'[\s_-]*[0-9]+[a-z]*$', '', name_part, flags=re.IGNORECASE)
    
    # Capitalize each word
    words = [word.capitalize() for word in name_part.split() if len(word) > 1]
    
    if words:
        return ' '.join(words)
    
    # Fallback: just return cleaned email prefix
    return email_address.split('@')[0].replace('.', ' ').replace('_', ' ').title()


def parse_indeed_email(body: str, subject: str) -> Optional[Dict]:
    """
    Parse Indeed job portal application emails with enhanced extraction.
    Extracts all available candidate info including LinkedIn, education.
    """
    body_lower = body.lower()
    
    # Check if this is an Indeed email
    if 'indeed' not in body_lower and 'indeed' not in subject.lower():
        return None
    
    result = {
        'name': '',
        'email': '',
        'phone': '',
        'location': '',
        'experience': 0,
        'skills': [],
        'summary': '',
        'linkedin': '',
        'education': [],
        'source': 'Indeed'
    }
    
    # Clean HTML from body
    clean_body = clean_html_to_text(body)
    
    # Common Indeed email patterns:
    # "John Smith has applied to your job: Software Engineer"
    # "New applicant: John Smith for Software Engineer"
    
    # Extract candidate name from subject patterns
    name_patterns = [
        r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(?:has applied|applied|just applied)',
        r'New applicant[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        r'Application from[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+is interested',
        r'Applicant:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    ]
    
    for pattern in name_patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            result['name'] = match.group(1).strip()
            break
    
    # Try to find name in body if not found in subject
    if not result['name']:
        body_name_patterns = [
            r'Applicant[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            r'Candidate[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            r'Name[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            r'From[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        ]
        for pattern in body_name_patterns:
            match = re.search(pattern, clean_body)
            if match:
                result['name'] = match.group(1).strip()
                break
    
    # Extract email from body
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', clean_body)
    if email_match:
        result['email'] = email_match.group()
    
    # Extract LinkedIn URL
    linkedin_match = re.search(r'(?:https?://)?(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-]+)', body, re.IGNORECASE)
    if linkedin_match:
        result['linkedin'] = f"https://linkedin.com/in/{linkedin_match.group(1)}"
    
    # Extract phone from body (multiple formats, minimum 7 digits to avoid years)
    phone_patterns = [
        r'\+971[\s.-]?\d{1,2}[\s.-]?\d{3}[\s.-]?\d{4}',  # UAE format first
        r'\+\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}',  # International
        r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}',  # US format
        r'(?:phone|mobile|cell|tel)[:\s]+([\d\s\-\.\+\(\)]{7,20})',  # Labeled phone
    ]
    for pattern in phone_patterns:
        phone_match = re.search(pattern, clean_body, re.IGNORECASE)
        if phone_match:
            phone_candidate = phone_match.group(1) if phone_match.lastindex else phone_match.group()
            # Filter out year-like numbers - must have at least 7 digits
            digits_only = re.sub(r'\D', '', phone_candidate)
            if len(digits_only) >= 7:
                result['phone'] = phone_candidate.strip()
                break
    
    # Extract location from body
    location_patterns = [
        r'Location[:\s]+([A-Za-z\s,]+?)(?:\n|$|\.)',
        r'City[:\s]+([A-Za-z\s,]+?)(?:\n|$|\.)',
        r'(?:based in|located in|from)\s+([A-Za-z\s,]+?)(?:\n|$|\.)',
    ]
    for pattern in location_patterns:
        loc_match = re.search(pattern, clean_body, re.IGNORECASE)
        if loc_match:
            result['location'] = loc_match.group(1).strip()[:50]
            break
    
    # Extract experience years
    exp_patterns = [
        r'(\d+)\+?\s*years?\s+(?:of\s+)?experience',
        r'experience[:\s]+(\d+)\+?\s*years?',
        r'(\d+)\+?\s*years?\s+in\s+',
    ]
    for pattern in exp_patterns:
        exp_match = re.search(pattern, clean_body, re.IGNORECASE)
        if exp_match:
            try:
                result['experience'] = int(exp_match.group(1))
            except:
                pass
            break
    
    # Extract skills from body
    skill_keywords = [
        'python', 'java', 'javascript', 'react', 'node', 'sql', 'aws', 'docker',
        'kubernetes', 'devops', 'agile', 'scrum', 'git', 'ci/cd', 'api',
        'typescript', 'vue', 'angular', 'django', 'flask', 'spring',
        'mongodb', 'postgresql', 'redis', 'machine learning', 'data science',
        'excel', 'marketing', 'sales', 'customer service', 'management'
    ]
    found_skills = [skill.title() for skill in skill_keywords if skill in body_lower]
    result['skills'] = list(set(found_skills))
    
    # Use cleaned body as summary
    result['summary'] = clean_body[:500] if clean_body else ''
    
    return result if result['name'] or result['email'] else None


def parse_linkedin_email(body: str, subject: str) -> Optional[Dict]:
    """Parse LinkedIn job application notification emails with enhanced extraction"""
    body_lower = body.lower()
    
    if 'linkedin' not in body_lower and 'linkedin' not in subject.lower():
        return None
    
    result = {
        'name': '',
        'email': '',
        'phone': '',
        'location': '',
        'skills': [],
        'summary': '',
        'linkedin': '',
        'experience': 0,
        'education': [],
        'source': 'LinkedIn'
    }
    
    clean_body = clean_html_to_text(body)
    
    # LinkedIn patterns: "John Smith applied to your job: ..."
    name_patterns = [
        r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+applied',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+has applied',
        r'New applicant[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        r'Applicant:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    ]
    
    for pattern in name_patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            result['name'] = match.group(1).strip()
            break
    
    # Try body if not found in subject
    if not result['name']:
        for pattern in name_patterns:
            match = re.search(pattern, clean_body, re.IGNORECASE)
            if match:
                result['name'] = match.group(1).strip()
                break
    
    # Extract email
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', clean_body)
    if email_match:
        result['email'] = email_match.group()
    
    # Extract LinkedIn profile URL
    linkedin_patterns = [
        r'(?:https?://)?(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-]+)',
        r'View\s+(?:profile|applicant).*?(linkedin\.com/in/[a-zA-Z0-9\-]+)',
    ]
    for pattern in linkedin_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            profile_id = match.group(1)
            if not profile_id.startswith('http'):
                result['linkedin'] = f"https://linkedin.com/in/{profile_id}"
            else:
                result['linkedin'] = f"https://{profile_id}"
            break
    
    # Extract phone
    phone_patterns = [
        r'\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}',
        r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}',
    ]
    for pattern in phone_patterns:
        match = re.search(pattern, clean_body)
        if match:
            result['phone'] = match.group()
            break
    
    # Extract location
    location_patterns = [
        r'Location[:\s]+([A-Za-z\s,]+?)(?:\n|$|\.)',
        r'(?:based in|located in|from)\s+([A-Za-z\s,]+?)(?:\n|$|\.)',
        r'Current location[:\s]+([A-Za-z\s,]+?)(?:\n|$|\.)',
    ]
    for pattern in location_patterns:
        match = re.search(pattern, clean_body, re.IGNORECASE)
        if match:
            result['location'] = match.group(1).strip()[:50]
            break
    
    # Extract experience
    exp_patterns = [
        r'(\d+)\+?\s*years?\s+(?:of\s+)?experience',
        r'experience[:\s]+(\d+)\+?\s*years?',
    ]
    for pattern in exp_patterns:
        match = re.search(pattern, clean_body, re.IGNORECASE)
        if match:
            try:
                result['experience'] = int(match.group(1))
            except:
                pass
            break
    
    # Extract skills
    skill_keywords = [
        'python', 'java', 'javascript', 'react', 'node', 'sql', 'aws', 'docker',
        'kubernetes', 'devops', 'agile', 'scrum', 'git', 'ci/cd', 'api',
        'typescript', 'vue', 'angular', 'django', 'flask', 'spring',
        'mongodb', 'postgresql', 'redis', 'machine learning', 'data science',
        'excel', 'marketing', 'sales', 'customer service', 'management',
        'figma', 'photoshop', 'leadership', 'communication'
    ]
    found_skills = [skill.title() for skill in skill_keywords if skill in body_lower]
    result['skills'] = list(set(found_skills))
    
    result['summary'] = clean_body[:500]
    
    return result if result['name'] or result['email'] else None


def extract_education_from_text(text: str) -> List[Dict]:
    """Extract structured education information from text - IMPROVED"""
    education = []
    
    # Better education patterns - require minimum field length
    edu_patterns = [
        # Full patterns with institution
        (r'(?:ph\.?d\.?|doctorate?)\s+(?:in\s+)?([A-Za-z\s]{4,40})\s+(?:from\s+)?([A-Za-z\s]+(?:University|College|Institute))', 'PhD'),
        (r"(?:master'?s?|m\.?s\.?|mba|m\.?tech)\s+(?:in\s+|of\s+)?([A-Za-z\s]{4,40})\s+(?:from\s+)?([A-Za-z\s]+(?:University|College|Institute))", 'Masters'),
        (r"(?:bachelor'?s?|b\.?s\.?|b\.?a\.?|b\.?e\.?|b\.?tech)\s+(?:in\s+|of\s+)?([A-Za-z\s]{4,40})\s+(?:from\s+)?([A-Za-z\s]+(?:University|College|Institute))", 'Bachelors'),
        # Patterns without institution
        (r'(?:ph\.?d\.?|doctorate?)\s+(?:in\s+)?([A-Za-z\s]{4,30})(?:\s|,|$)', 'PhD'),
        (r"(?:master'?s?|m\.?s\.?|mba)\s+(?:in\s+)?([A-Za-z\s]{4,30})(?:\s|,|$)", 'Masters'),
        (r"(?:bachelor'?s?|b\.?e\.?|b\.?tech|b\.?s\.?)\s+(?:in\s+|of\s+)?([A-Za-z\s]{4,30})(?:\s|,|$)", 'Bachelors'),
    ]
    
    for pattern, degree_type in edu_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            field = match.group(1).strip() if match.group(1) else ''
            institution = match.group(2).strip() if len(match.groups()) > 1 and match.group(2) else ''
            # Clean up field
            field = re.sub(r'\b(in|of|from|the|and|with)\b', '', field, flags=re.IGNORECASE).strip()
            # Validate field isn't garbage
            if len(field) >= 3 and not re.match(r'^[a-z]{1,3}\s*$', field.lower()):
                education.append({
                    'degree': degree_type,
                    'field': field.title(),
                    'institution': institution.title() if institution else '',
                    'year': ''
                })
                break
    
    # Also try to find university names directly
    if not education:
        uni_patterns = [
            r'graduated\s+from\s+([A-Z][a-zA-Z\s]+(?:University|College|Institute))',
            r'studied\s+at\s+([A-Z][a-zA-Z\s]+(?:University|College|Institute))',
        ]
        for pattern in uni_patterns:
            match = re.search(pattern, text)
            if match:
                education.append({
                    'degree': 'Degree',
                    'field': '',
                    'institution': match.group(1).strip(),
                    'year': ''
                })
                break
    
    return education


def is_valid_name(name: str) -> bool:
    """
    Check if a string looks like a valid human name.
    Returns False for garbage data like dates, IDs, random strings.
    """
    if not name or len(name) < 2:
        return False
    
    # Too short or too long
    if len(name) < 2 or len(name) > 100:
        return False
    
    # Contains too many numbers (likely a date or ID)
    digit_count = sum(1 for c in name if c.isdigit())
    if digit_count > 4 or digit_count > len(name) * 0.3:
        return False
    
    # Contains patterns that look like dates (e.g., "2 0 1 3 - 2 0 1 5")
    if re.search(r'\d{4}\s*[-–]\s*\d{4}', name.replace(' ', '')):
        return False
    if re.search(r'[0-9]\s+[0-9]\s+[0-9]\s+[0-9]', name):
        return False
    
    # Contains too many special characters
    special_count = sum(1 for c in name if not c.isalnum() and c not in ' .-\'')
    if special_count > 3:
        return False
    
    # Looks like an email address
    if '@' in name:
        return False
    
    # Looks like a UUID or hash
    if re.match(r'^[a-f0-9]{8,}$', name.lower().replace('-', '')):
        return False
    
    # Looks like a conversation ID
    if re.match(r'^conversation', name, re.IGNORECASE):
        return False
    
    # At least one letter required
    if not any(c.isalpha() for c in name):
        return False
    
    # Filter out common system/company names that aren't real candidates
    invalid_names = [
        'employers', 'employer', 'indeed', 'linkedin', 'glassdoor', 'noreply',
        'no-reply', 'notifications', 'notification', 'jobs', 'careers', 'hiring',
        'recruitment', 'recruiter', 'hr', 'human resources', 'support', 'info',
        'admin', 'administrator', 'system', 'automated', 'donotreply', 'mailer',
        'daemon', 'postmaster', 'webmaster', 'team', 'service', 'services'
    ]
    if name.lower().strip() in invalid_names:
        return False
    
    # Must have at least 2 parts (first + last name) or be > 3 chars
    parts = name.split()
    if len(parts) == 1 and len(name) < 4:
        return False
    
    return True

load_dotenv()

logger = logging.getLogger(__name__)

class EmailAccount:
    """Configuration for a single email account"""
    def __init__(self, name: str, server: str, port: int, email: str, password: str):
        self.name = name
        self.server = server
        self.port = port
        self.email = email
        self.password = password
        self.processed_count = 0
        self.last_check = None

class EmailScraperService:
    def __init__(self):
        self.resume_parser = ResumeParser()
        self.email_parser = EmailParser()
        # NOTE: OpenAI removed - job categorization now uses local keyword matching (zero API cost)
        
        # Multiple email accounts
        self.email_accounts = self._load_email_accounts()
        
        # Processing state (shared across accounts)
        self.processed_message_ids = set()
        self.process_all_history = True  # Process all existing emails
        
    def _load_email_accounts(self) -> List[EmailAccount]:
        """Load multiple email accounts from environment"""
        accounts = []
        
        # Primary account (backward compatible)
        if os.getenv('EMAIL_ADDRESS'):
            accounts.append(EmailAccount(
                name="Primary",
                server=os.getenv('IMAP_SERVER', 'imap.gmail.com'),
                port=int(os.getenv('IMAP_PORT', '993')),
                email=os.getenv('EMAIL_ADDRESS'),
                password=os.getenv('EMAIL_PASSWORD')
            ))
        
        # Load additional accounts (EMAIL_1_, EMAIL_2_, etc.)
        i = 1
        while True:
            email = os.getenv(f'EMAIL_{i}_ADDRESS')
            if not email:
                break
            
            accounts.append(EmailAccount(
                name=f"Account {i}",
                server=os.getenv(f'EMAIL_{i}_SERVER', 'imap.gmail.com'),
                port=int(os.getenv(f'EMAIL_{i}_PORT', '993')),
                email=email,
                password=os.getenv(f'EMAIL_{i}_PASSWORD')
            ))
            i += 1
        
        print(f"📧 Loaded {len(accounts)} email account(s)")
        return accounts
    
    def connect_to_inbox(self, account: EmailAccount):
        """Connect to specific email account via IMAP with timeout"""
        try:
            import socket
            # Set shorter timeout for socket operations (10 seconds)
            socket.setdefaulttimeout(10)
            
            mail = imaplib.IMAP4_SSL(account.server, account.port)
            mail.login(account.email, account.password)
            mail.select('INBOX')
            
            # Reset to no timeout after successful connection
            socket.setdefaulttimeout(None)
            return mail
        except Exception as e:
            logger.warning(f"❌ Connection failed for {account.name} ({account.email}): {str(e)[:100]}")
            return None
    
    async def fetch_emails(self, mail, process_all: bool = False, since_date=None) -> List[Dict]:
        """
        Fetch emails from mailbox
        process_all=True: Fetch ALL emails from beginning
        process_all=False: Fetch only UNSEEN (new) emails
        """
        try:
            # Determine search criteria based on parameters
            if process_all:
                # Get ALL emails from the beginning
                search_criteria = 'ALL'
            elif since_date:
                date_str = since_date.strftime("%d-%b-%Y")
                search_criteria = f'(SINCE "{date_str}")'
            else:
                # Get only unseen (new) emails by default
                search_criteria = 'UNSEEN'
            
            status, messages = mail.search(None, search_criteria)
            
            if status != 'OK':
                return []
            
            email_ids = messages[0].split()
            total_emails = len(email_ids)
            logger.info(f"📬 Found {total_emails} emails to process...")
            
            # Track filtering stats
            total_checked = 0
            filtered_count = 0
            
            # Process only new emails (incremental)
            new_emails = []
            for idx, email_id in enumerate(email_ids, 1):
                # Progress logging for large fetches
                if total_emails > 100 and idx % 50 == 0:
                    logger.info(f"📊 Fetching progress: {idx}/{total_emails} emails checked...")
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                
                if status != 'OK':
                    continue
                
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        total_checked += 1
                        
                        # Get unique message ID to prevent duplicates
                        message_id = msg.get('Message-ID')
                        if message_id in self.processed_message_ids:
                            continue
                        
                        email_data = await self.parse_email_message(msg)
                        if email_data:
                            email_data['email_id'] = email_id.decode()
                            email_data['message_id'] = message_id
                            new_emails.append(email_data)
                            self.processed_message_ids.add(message_id)
                        else:
                            filtered_count += 1
            
            if filtered_count > 0:
                logger.info(f"📧 Email filter: {total_checked} checked, {len(new_emails)} job applications, {filtered_count} filtered out")
            
            return new_emails
            
        except Exception as e:
            logger.error(f"Error fetching emails: {str(e)[:100]}")
            return []
    
    async def parse_email_message(self, msg):
        """Extract candidate data from email"""
        try:
            # Decode subject
            subject = ""
            if msg.get("Subject"):
                try:
                    subject_parts = decode_header(msg["Subject"])
                    subject = ""
                    for content, encoding in subject_parts:
                        if isinstance(content, bytes):
                            # Handle unknown or invalid encodings
                            if encoding and encoding.lower() not in ['unknown-8bit', 'unknown']:
                                try:
                                    subject += content.decode(encoding, errors='ignore')
                                except (LookupError, AttributeError):
                                    # Invalid encoding name, fallback to utf-8
                                    subject += content.decode('utf-8', errors='ignore')
                            else:
                                # Unknown encoding, try utf-8 then latin-1
                                try:
                                    subject += content.decode('utf-8', errors='ignore')
                                except:
                                    subject += content.decode('latin-1', errors='ignore')
                        else:
                            subject += str(content)
                except Exception as decode_error:
                    # Fallback: extract raw subject as string
                    try:
                        raw_subject = msg.get("Subject", "")
                        if isinstance(raw_subject, bytes):
                            subject = raw_subject.decode('utf-8', errors='ignore')
                        else:
                            subject = str(raw_subject)
                    except:
                        subject = "(no subject)"
            
            # Filter out non-job-application emails based on subject
            job_keywords = ['resume', 'cv', 'application', 'apply', 'applying', 'position', 
                           'job', 'candidate', 'opportunity', 'career', 'hiring', 'employment',
                           'vacancy', 'interview', 'interested', 'role', 'post', 'opening',
                           # Indeed specific keywords
                           'indeed', 'applied to your job', 'new applicant', 'application received',
                           'has applied', 'just applied']
            
            subject_lower = subject.lower()
            has_job_keyword = any(keyword in subject_lower for keyword in job_keywords)
            
            # Check if from job portal (Indeed, LinkedIn, etc.)
            is_job_portal = any(portal in sender_email.lower() for portal in 
                               ['indeed.com', 'linkedin.com', 'glassdoor.com', 'ziprecruiter.com', 
                                'monster.com', 'naukri.com', 'bayt.com', 'gulftalent.com'])
            
            # Skip emails that clearly don't look like job applications
            skip_keywords = ['newsletter', 'unsubscribe', 'notification', 'alert', 'update',
                           'confirmation', 'receipt', 'invoice', 'payment', 'order', 'shipping']
            should_skip = any(keyword in subject_lower for keyword in skip_keywords)
            
            if should_skip and not has_job_keyword and not is_job_portal:
                # Skip newsletters, notifications, etc.
                return None
            
            # Extract sender
            from_email = msg.get("From", "")
            sender_email = email.utils.parseaddr(from_email)[1]
            sender_name = email.utils.parseaddr(from_email)[0]
            
            # Extract email date
            date_str = msg.get("Date", "")
            try:
                from email.utils import parsedate_to_datetime
                received_date = parsedate_to_datetime(date_str)
            except:
                received_date = datetime.now()
            
            # Extract body
            body = ""
            attachments = []
            
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    
                    # Get email body
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        try:
                            body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            pass
                    
                    # Get attachments (resumes)
                    if "attachment" in content_disposition:
                        filename = part.get_filename()
                        if filename and (filename.endswith('.pdf') or filename.endswith('.docx')):
                            file_data = part.get_payload(decode=True)
                            attachments.append({
                                'filename': filename,
                                'data': file_data
                            })
            else:
                try:
                    body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                except:
                    pass
            
            # Final filter: Check if email has resume attachment OR job application content
            body_lower = body.lower()
            has_resume_attachment = any(att['filename'].lower().endswith(('.pdf', '.docx', '.doc')) 
                                       for att in attachments)
            
            body_keywords = ['resume', 'cv', 'curriculum vitae', 'applying for', 'application for',
                           'attached is my', 'please find attached', 'my resume', 'my cv',
                           'years of experience', 'skills include', 'qualified for', 'interested in the']
            has_application_content = any(keyword in body_lower for keyword in body_keywords)
            
            # Skip if it doesn't have resume attachment AND doesn't have application content
            if not has_resume_attachment and not has_application_content and not has_job_keyword:
                return None
            
            return {
                'subject': subject,
                'sender_email': sender_email,
                'sender_name': sender_name,
                'body': body,
                'attachments': attachments,
                'received_date': received_date
            }
            
        except Exception as e:
            logger.warning(f"Error parsing email: {str(e)[:100]}")
            return None
    
    async def extract_candidate_from_email(self, email_data: Dict) -> Optional[Dict]:
        """
        Use AI to extract candidate information from email
        Returns candidate dict ready for database
        """
        try:
            # Clean the email body (strip HTML)
            raw_body = email_data.get('body', '')
            clean_body = clean_html_to_text(raw_body) if '<' in raw_body else raw_body
            subject = email_data.get('subject', '')
            sender_email = email_data.get('sender_email', '')
            
            # FILTER: Skip system/noreply emails that aren't real candidates
            skip_email_patterns = [
                r'noreply@', r'no-reply@', r'donotreply@', r'notification@',
                r'employers-noreply@', r'mailer-daemon@', r'postmaster@',
                r'support@indeed', r'jobs@indeed', r'@indeedemail\.com',
                r'notification@linkedin', r'messages-noreply@linkedin'
            ]
            for pattern in skip_email_patterns:
                if re.search(pattern, sender_email, re.IGNORECASE):
                    # This is a system email - we need to extract the REAL candidate from the body
                    logger.info(f"📧 System email detected: {sender_email} - will extract candidate from body")
                    break
            
            # FIRST: Try LLM-powered email parsing (100% accurate)
            llm_portal_data = None
            try:
                from services.llm_service import get_llm_service
                llm_service = await get_llm_service()
                if llm_service.available:
                    llm_portal_data = await llm_service.parse_candidate_email(
                        subject=subject,
                        body=clean_body[:4000],
                        sender=sender_email
                    )
                    if llm_portal_data:
                        logger.info(f"🤖 LLM parsed email: {llm_portal_data.get('name', 'Unknown')} | Source: {llm_portal_data.get('source', 'Unknown')}")
            except Exception as llm_err:
                logger.debug(f"LLM email parsing skipped: {llm_err}")
            
            # SECOND: Try regex-based job portal parsing as fallback
            job_portal_data = llm_portal_data  # Use LLM result if available
            portal_source = llm_portal_data.get('source') if llm_portal_data else None
            
            if not job_portal_data:
                # Check Indeed
                indeed_data = parse_indeed_email(raw_body, subject)
                if indeed_data:
                    job_portal_data = indeed_data
                    portal_source = 'Indeed'
                    logger.info(f"📧 Parsed Indeed application: {indeed_data.get('name', 'Unknown')}")
                
                # Check LinkedIn
                if not job_portal_data:
                    linkedin_data = parse_linkedin_email(raw_body, subject)
                    if linkedin_data:
                        job_portal_data = linkedin_data
                        portal_source = 'LinkedIn'
                        logger.info(f"📧 Parsed LinkedIn application: {linkedin_data.get('name', 'Unknown')}")
            
            # Parse resume if attached
            resume_data = None
            resume_file_data = None
            resume_filename = None
            
            if email_data.get('attachments'):
                for attachment in email_data['attachments']:
                    try:
                        filename = attachment.get('filename', '')
                        file_data = attachment.get('data')
                        
                        if not file_data:
                            continue
                            
                        # Check if it's a resume file
                        if filename.lower().endswith(('.pdf', '.docx', '.doc')):
                            resume_data = await self.resume_parser.parse_resume(
                                file_data,
                                filename
                            )
                            # Store the raw file for download
                            resume_file_data = file_data
                            resume_filename = filename
                            logger.info(f"📄 Parsed resume: {filename}")
                            break
                    except Exception as parse_err:
                        logger.warning(f"Error parsing attachment {attachment.get('filename', 'unknown')}: {str(parse_err)[:50]}")
                        continue
            
            # If no resume, parse email body (use cleaned body)
            if not resume_data:
                resume_data = self.email_parser.parse_email_application(
                    clean_body,
                    sender_email
                )
            
            # Merge job portal data with resume data (job portal takes priority for contact info)
            if job_portal_data:
                # Use job portal name/email if available, otherwise fall back to resume
                if job_portal_data.get('name') and is_valid_name(job_portal_data['name']):
                    resume_data['name'] = job_portal_data['name']
                if job_portal_data.get('email'):
                    # Use candidate's actual email from job portal, not the portal's noreply
                    if '@indeed.com' not in job_portal_data['email'].lower():
                        sender_email = job_portal_data['email']
                if job_portal_data.get('phone'):
                    resume_data['phone'] = job_portal_data['phone']
                if job_portal_data.get('location'):
                    resume_data['location'] = job_portal_data['location']
                if job_portal_data.get('skills'):
                    resume_data['skills'] = list(set(resume_data.get('skills', []) + job_portal_data['skills']))
                if job_portal_data.get('experience'):
                    resume_data['experience'] = job_portal_data['experience']
            
            # Determine actual candidate email early (needed for ID generation)
            actual_candidate_email = sender_email
            if job_portal_data and job_portal_data.get('email'):
                portal_email = job_portal_data['email']
                # Make sure it's not a system email
                if '@indeed' not in portal_email.lower() and 'noreply' not in portal_email.lower():
                    actual_candidate_email = portal_email
            
            # Generate unique candidate ID based on actual candidate email
            candidate_id = hashlib.md5(actual_candidate_email.encode()).hexdigest()
            
            # Use AI to infer job category/role
            job_category = await self.infer_job_category(email_data, resume_data)
            
            # Determine best name:
            # 1. From job portal data (Indeed/LinkedIn) - most reliable
            # 2. From resume if available and looks valid
            # 3. From sender name if it looks like a real name (not conversation ID)
            # 4. Extract from email address as last resort
            candidate_name = None
            
            # Try job portal name first (highest priority)
            if job_portal_data and job_portal_data.get('name') and is_valid_name(job_portal_data['name']):
                candidate_name = job_portal_data['name']
            
            # Try resume name
            if not candidate_name:
                resume_name = resume_data.get('name', '')
                if resume_name and is_valid_name(resume_name):
                    candidate_name = resume_name
            
            # Try sender name if not a system-generated name
            if not candidate_name:
                sender_name = email_data.get('sender_name', '')
                if sender_name and is_valid_name(sender_name):
                    candidate_name = sender_name
            
            # Fallback: extract from candidate's actual email (not system email)
            if not candidate_name:
                # Use job portal email if available, otherwise sender email
                actual_email = sender_email
                if job_portal_data and job_portal_data.get('email') and '@indeed' not in job_portal_data['email'].lower():
                    actual_email = job_portal_data['email']
                
                email_name = extract_name_from_email_address(actual_email)
                if is_valid_name(email_name):
                    candidate_name = email_name
                else:
                    # Last resort: use email prefix cleaned up
                    candidate_name = actual_email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
            
            # FINAL VALIDATION: Skip if name is still invalid
            if not candidate_name or not is_valid_name(candidate_name):
                logger.warning(f"⚠️ Skipping candidate - invalid name: {candidate_name}")
                return None
            
            # Note: actual_candidate_email was already determined earlier (before ID generation)
            
            # Clean summary - use clean body text
            summary = resume_data.get('summary', '') or clean_body[:500]
            # Make sure summary doesn't have HTML
            if '<' in summary:
                summary = clean_html_to_text(summary)
            
            # Get raw text for AI analysis (prefer resume text over email body)
            raw_text = resume_data.get('raw_text', '') or clean_body[:1000]
            
            # Get education and work history, convert to JSON-serializable format
            education = resume_data.get('education', [])
            if isinstance(education, list):
                education = json.dumps(education)
            
            work_history = resume_data.get('work_history', [])
            if isinstance(work_history, list):
                work_history_json = work_history
            else:
                work_history_json = []
            
            candidate = {
                'id': candidate_id,
                'email': actual_candidate_email,  # Use candidate's actual email, not system email
                'name': candidate_name,
                'phone': resume_data.get('phone', ''),
                'location': resume_data.get('location', ''),
                'skills': resume_data.get('skills', []),
                'experience': resume_data.get('experience', 0),
                'education': education,
                'summary': summary,
                'resume_text': raw_text,  # For AI analysis
                'workHistory': work_history_json,
                'linkedin': resume_data.get('linkedin', ''),
                'status': 'New',
                'matchScore': 0,  # Will be calculated by AI
                'appliedDate': email_data['received_date'].isoformat(),
                'job_category': job_category,
                'raw_email_subject': email_data['subject'],
                'last_updated': datetime.now().isoformat(),
                # Resume file for download
                'resume_file_data': resume_file_data,
                'resume_filename': resume_filename
            }
            
            return candidate
            
        except Exception as e:
            logger.warning(f"Error extracting candidate: {e}")
            return None
    
    async def infer_job_category(self, email_data: Dict, resume_data: Dict) -> str:
        """
        Determine job category using LOCAL keyword matching only (NO OpenAI calls)
        This saves API costs - job categorization doesn't need expensive AI
        """
        # Build text to analyze
        text = f"{email_data.get('subject', '')} {email_data.get('body', '')[:500]} {' '.join(resume_data.get('skills', []))}"
        text_lower = text.lower()
        
        # Check for job portal source first
        if resume_data.get('source') == 'indeed':
            job_title = resume_data.get('job_title_applied', '')
            if job_title:
                return job_title
        
        # Comprehensive keyword matching for job categories
        categories = {
            'Software Engineer': ['software engineer', 'developer', 'programmer', 'full stack', 'backend', 'frontend', 'web developer', 'mobile developer', 'ios', 'android'],
            'DevOps Engineer': ['devops', 'sre', 'site reliability', 'infrastructure', 'kubernetes', 'docker', 'ci/cd', 'cloud engineer'],
            'Data Scientist': ['data scientist', 'machine learning', 'ml engineer', 'ai engineer', 'deep learning', 'data analyst', 'analytics'],
            'Data Engineer': ['data engineer', 'etl', 'data pipeline', 'spark', 'hadoop', 'airflow', 'big data'],
            'QA Engineer': ['qa', 'quality assurance', 'test engineer', 'automation test', 'selenium', 'testing'],
            'Product Manager': ['product manager', 'product owner', 'scrum master', 'agile'],
            'UI/UX Designer': ['ui designer', 'ux designer', 'product designer', 'figma', 'sketch', 'user experience'],
            'Marketing Manager': ['marketing', 'digital marketing', 'seo', 'social media', 'content marketing', 'brand'],
            'Sales Executive': ['sales', 'business development', 'account manager', 'client relations'],
            'HR Manager': ['hr', 'human resources', 'recruiter', 'talent acquisition', 'people operations'],
            'Finance Analyst': ['finance', 'accountant', 'accounting', 'financial analyst', 'bookkeeper', 'auditor'],
            'Project Manager': ['project manager', 'program manager', 'pmo', 'project coordinator'],
            'System Administrator': ['system admin', 'sysadmin', 'it support', 'network admin', 'helpdesk'],
            'Security Engineer': ['security engineer', 'cybersecurity', 'infosec', 'penetration test', 'soc analyst'],
            'Database Administrator': ['dba', 'database admin', 'sql server', 'oracle dba', 'mysql admin'],
        }
        
        # Find best matching category
        for category, keywords in categories.items():
            if any(keyword in text_lower for keyword in keywords):
                return category
        
        # Fallback to skill-based categorization
        skills = resume_data.get('skills', [])
        skills_lower = ' '.join(skills).lower()
        
        if any(s in skills_lower for s in ['python', 'java', 'javascript', 'react', 'node', 'c++', 'c#']):
            return 'Software Engineer'
        elif any(s in skills_lower for s in ['docker', 'kubernetes', 'aws', 'azure', 'terraform']):
            return 'DevOps Engineer'
        elif any(s in skills_lower for s in ['machine learning', 'tensorflow', 'pytorch', 'pandas']):
            return 'Data Scientist'
        elif any(s in skills_lower for s in ['sql', 'tableau', 'power bi', 'excel']):
            return 'Data Analyst'
        elif any(s in skills_lower for s in ['figma', 'sketch', 'adobe xd', 'photoshop']):
            return 'UI/UX Designer'
        
        return 'General'
    
    async def process_batch(self, candidates: List[Dict], db_connection):
        """
        Process a batch of candidates efficiently
        - Check for duplicates by email
        - Update existing or insert new
        - Only process NEW candidates for matching
        """
        processed = []
        updated = []
        new_candidates = []
        
        for candidate in candidates:
            try:
                # Check if candidate exists (by email hash)
                existing = await db_connection.get_candidate_by_email(candidate['email'])
                
                if existing:
                    # UPDATE existing candidate
                    candidate['id'] = existing['id']
                    candidate['appliedDate'] = existing['appliedDate']  # Keep original application date
                    await db_connection.update_candidate(candidate)
                    updated.append(candidate)
                else:
                    # INSERT new candidate
                    await db_connection.insert_candidate(candidate)
                    new_candidates.append(candidate)
                
                processed.append(candidate)
                
            except Exception as e:
                print(f"Error processing candidate {candidate.get('email')}: {e}")
        
        return {
            'processed': len(processed),
            'new': len(new_candidates),
            'updated': len(updated),
            'new_candidates': new_candidates  # Only these need AI matching
        }
    
    async def run_continuous_scraper(self, interval_seconds: int = 60):
        """ALL email accounts
        - First run: Process ALL historical emails
        - Subsequent runs: Process only NEW emails
        """
        print(f"🔄 Multi-account email scraper started")
        print(f"📧 Monitoring {len(self.email_accounts)} email account(s)")
        print(f"⏱️  Checking every {interval_seconds} seconds")
        
        first_run = True
        
        while True:
            try:
                for account in self.email_accounts:
                    try:
                        mail = self.connect_to_inbox(account)
                        if not mail:
                            continue
                        
                        # First run: process ALL emails. After: only NEW
                        process_all = first_run and self.process_all_history
                        new_emails = await self.fetch_emails(mail, process_all=process_all)
                        
                        if new_emails:
                            print(f"📧 [{account.name}] Found {len(new_emails)} emails to process")
                            
                            # Extract candidates from emails
                            candidates = []
                            for email_data in new_emails:
                                candidate = await self.extract_candidate_from_email(email_data)
                                if candidate:
                                    candidates.append(candidate)
                            
                            print(f"👥 [{account.name}] Extracted {len(candidates)} candidates")
                            account.processed_count += len(candidates)
                            
                            # TODO: Process batch and save to database
                            # result = await self.process_batch(candidates, db_connection)
                            # print(f"✅ Processed: {result['new']} new, {result['updated']} updated")
                        
                        mail.logout()
                        account.last_check = datetime.now()
                        
                    except Exception as e:
                        print(f"❌ [{account.name}] Error: {e}")
                
                first_run = False  # After first run, only process new emailslast_check = datetime.now()
                
                await asyncio.sleep(interval_seconds)
                
            except Exception as e:
                print(f"❌ Scraper error: {e}")
                await asyncio.sleep(interval_seconds)

# Singleton instance
_scraper_service = None

def get_scraper_service():
    global _scraper_service
    if _scraper_service is None:
        _scraper_service = EmailScraperService()
    return _scraper_service

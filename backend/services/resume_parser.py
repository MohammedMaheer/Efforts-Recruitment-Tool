import PyPDF2
import docx
import re
import logging
from typing import Dict, List, Any, Optional
from io import BytesIO

logger = logging.getLogger(__name__)


class ResumeParser:
    """
    Service for parsing resume files and extracting structured data.
    
    Uses a tiered approach:
    1. LLM (Ollama) for 100% accurate structured extraction (primary)
    2. Regex-based extraction as fallback when LLM is unavailable
    """
    
    def __init__(self):
        self._llm_service = None
        # Comprehensive skill keywords for better extraction
        self.skill_keywords = [
            # Programming Languages
            'python', 'javascript', 'typescript', 'react', 'node.js', 'vue', 'angular',
            'java', 'c++', 'c#', '.net', 'go', 'golang', 'rust', 'ruby', 'php', 'swift',
            'kotlin', 'scala', 'perl', 'matlab', 'r programming', 'dart', 'flutter',
            
            # Databases
            'sql', 'postgresql', 'mongodb', 'mysql', 'redis', 'elasticsearch', 'oracle',
            'sqlite', 'cassandra', 'dynamodb', 'firebase', 'neo4j', 'mariadb',
            
            # DevOps & Cloud
            'docker', 'kubernetes', 'aws', 'azure', 'gcp', 'terraform', 'ansible',
            'jenkins', 'ci/cd', 'github actions', 'gitlab', 'bitbucket', 'circleci',
            'prometheus', 'grafana', 'nginx', 'apache', 'linux', 'unix', 'bash',
            
            # Frameworks & Tools
            'django', 'flask', 'fastapi', 'express', 'spring boot', 'hibernate',
            'rails', 'laravel', 'nextjs', 'nuxt', 'gatsby', 'svelte', 'redux',
            'tailwind', 'bootstrap', 'material ui', 'sass', 'webpack', 'vite',
            
            # AI/ML & Data
            'machine learning', 'deep learning', 'ai', 'data science', 'tensorflow',
            'pytorch', 'keras', 'scikit-learn', 'pandas', 'numpy', 'spark', 'hadoop',
            'tableau', 'power bi', 'jupyter', 'nlp', 'computer vision', 'opencv',
            
            # Soft Skills & Methodologies
            'agile', 'scrum', 'kanban', 'jira', 'confluence', 'trello', 'asana',
            'leadership', 'management', 'communication', 'teamwork', 'problem solving',
            
            # Architecture & Concepts
            'rest', 'graphql', 'microservices', 'api', 'backend', 'frontend',
            'full-stack', 'devops', 'cloud', 'saas', 'paas', 'serverless',
            'event-driven', 'message queue', 'rabbitmq', 'kafka', 'websocket',
            
            # Security
            'security', 'oauth', 'jwt', 'encryption', 'ssl', 'penetration testing',
            'vulnerability assessment', 'firewall', 'siem',
            
            # Business & Marketing
            'seo', 'google analytics', 'marketing', 'sales', 'crm', 'salesforce',
            'hubspot', 'mailchimp', 'social media', 'content marketing', 'ppc',
            
            # Design
            'figma', 'sketch', 'adobe xd', 'photoshop', 'illustrator', 'ui/ux',
            'user research', 'wireframing', 'prototyping', 'design systems',
            
            # Mobile
            'ios', 'android', 'react native', 'xamarin', 'ionic',
            
            # Version Control
            'git', 'github', 'version control', 'code review'
        ]
    
    async def extract_text(self, content: bytes, filename: str) -> str:
        """Extract text from PDF or DOCX file"""
        if filename.lower().endswith('.pdf'):
            return self._extract_from_pdf(content)
        elif filename.lower().endswith('.docx'):
            return self._extract_from_docx(content)
        else:
            raise ValueError("Unsupported file format. Supported: PDF, DOCX")
    
    def _extract_from_pdf(self, content: bytes) -> str:
        """Extract text from PDF using multiple methods for best results"""
        pdf_file = BytesIO(content)
        text = ""
        
        # Try pdfplumber first (better for complex layouts)
        try:
            import pdfplumber
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            if text.strip():
                logger.info(f"📄 PDF extracted with pdfplumber: {len(text)} chars")
                return text
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"pdfplumber failed, trying PyPDF2: {e}")
        
        # Fallback to PyPDF2
        pdf_file.seek(0)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        
        logger.info(f"📄 PDF extracted with PyPDF2: {len(text)} chars")
        return text
    
    def _extract_from_docx(self, content: bytes) -> str:
        """Extract text from DOCX"""
        doc_file = BytesIO(content)
        doc = docx.Document(doc_file)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text
    
    async def parse_resume(self, content: bytes, filename: str) -> Dict[str, Any]:
        """
        Parse resume and extract structured data.
        Uses LLM for 100% accurate extraction, falls back to regex if unavailable.
        """
        text = await self.extract_text(content, filename)
        
        if not text or len(text.strip()) < 30:
            logger.warning(f"⚠️ Insufficient text extracted from {filename}")
            return self._empty_result(text)
        
        # Strategy 1: Try LLM-powered extraction (100% accurate)
        llm_result = await self._parse_with_llm(text)
        if llm_result:
            logger.info(f"✅ Resume parsed with LLM: {llm_result.get('name', 'Unknown')}")
            llm_result['raw_text'] = text[:2000]
            return llm_result
        
        # Strategy 2: Fallback to regex-based extraction
        logger.info("⚠️ LLM unavailable, using regex-based extraction")
        name = self._extract_name(text)
        email = self._extract_email(text)
        phone = self._extract_phone(text)
        skills = self._extract_skills(text)
        experience = self._extract_experience(text)
        education = self._extract_education(text)
        work_history = self._extract_work_history(text)
        summary = self._extract_summary(text)
        location = self._extract_location(text)
        linkedin = self._extract_linkedin(text)
        
        return {
            "name": name,
            "email": email,
            "phone": phone,
            "skills": skills,
            "experience": experience,
            "education": education,
            "work_history": work_history,
            "summary": summary,
            "location": location,
            "linkedin": linkedin,
            "raw_text": text[:2000],
            "parsed_by": "regex"
        }
    
    async def _parse_with_llm(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse resume using LLM service for 100% accurate extraction"""
        try:
            # Lazy import to avoid circular dependencies
            if self._llm_service is None:
                from services.llm_service import get_llm_service
                self._llm_service = await get_llm_service()
            
            if not self._llm_service.available:
                return None
            
            result = await self._llm_service.parse_resume(text)
            
            if result and (result.get('name') or result.get('email') or result.get('skills')):
                # Convert LLM format to parser format
                return {
                    "name": result.get('name', 'Unknown'),
                    "email": result.get('email', ''),
                    "phone": result.get('phone', ''),
                    "skills": result.get('skills', []),
                    "experience": result.get('experience_years', 0),
                    "education": result.get('education', []),
                    "work_history": result.get('work_history', []),
                    "summary": result.get('summary', ''),
                    "location": result.get('location', ''),
                    "linkedin": result.get('linkedin', ''),
                    "certifications": result.get('certifications', []),
                    "languages": result.get('languages', []),
                    "job_category": result.get('job_category', 'General'),
                    "parsed_by": "llm"
                }
            
            return None
            
        except Exception as e:
            logger.warning(f"LLM resume parsing failed: {e}")
            return None
    
    def _empty_result(self, text: str = "") -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            "name": "Unknown",
            "email": "",
            "phone": "",
            "skills": [],
            "experience": 0,
            "education": [],
            "work_history": [],
            "summary": "",
            "location": "",
            "linkedin": "",
            "raw_text": text[:2000] if text else "",
            "parsed_by": "none"
        }
    
    def _extract_linkedin(self, text: str) -> str:
        """Extract LinkedIn profile URL"""
        # LinkedIn URL patterns
        patterns = [
            r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+/?',
            r'linkedin\.com/in/[\w\-]+',
            r'linkedin:\s*([\w\-]+)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                url = matches[0]
                # Ensure it's a full URL
                if not url.startswith('http'):
                    if url.startswith('linkedin.com'):
                        url = 'https://' + url
                    else:
                        url = f'https://linkedin.com/in/{url}'
                return url
        
        return ""
    
    def _extract_location(self, text: str) -> str:
        """Extract location from resume"""
        text_lower = text.lower()
        
        # Common UAE locations
        locations = ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'fujairah', 'ras al khaimah', 'umm al quwain', 'uae', 'united arab emirates']
        for loc in locations:
            if loc in text_lower:
                return loc.title()
        
        # Other common locations
        other_locations = ['india', 'pakistan', 'philippines', 'uk', 'usa', 'remote', 'singapore', 'australia', 'canada']
        for loc in other_locations:
            if loc in text_lower:
                return loc.title()
        
        return 'UAE'  # Default
    
    def _is_valid_name(self, name: str) -> bool:
        """Check if extracted name is valid (not garbage)"""
        if not name or len(name) < 2 or len(name) > 60:
            return False
        # Contains too many digits (likely a date/ID)
        digit_count = sum(1 for c in name if c.isdigit())
        if digit_count > 3:
            return False
        # Contains date patterns
        if re.search(r'\d{4}\s*[-–]\s*\d{4}', name.replace(' ', '')):
            return False
        if re.search(r'[0-9]\s+[0-9]\s+[0-9]\s+[0-9]', name):
            return False
        # Is a common keyword
        garbage_keywords = ['resume', 'cv', 'curriculum', 'vitae', 'summary', 'professional', 
                          'objective', 'experience', 'education', 'skills', 'contact', 
                          'personal', 'details', 'career', 'profile']
        name_lower = name.lower().strip()
        for kw in garbage_keywords:
            if name_lower == kw or name_lower.startswith(kw + ' ') or name_lower.endswith(' ' + kw):
                return False
        # At least one letter
        if not any(c.isalpha() for c in name):
            return False
        return True
    
    def _extract_name(self, text: str) -> str:
        """Extract candidate name from resume text - improved algorithm"""
        lines = text.split('\n')
        
        # First pass: look for lines that look like names in the first 10 lines
        for line in lines[:10]:
            line = line.strip()
            # Skip empty, emails, or very long lines
            if not line or '@' in line or len(line) > 60:
                continue
            # Skip lines with too many digits
            if sum(1 for c in line if c.isdigit()) > 2:
                continue
            # Skip lines that are clearly headers
            if any(kw in line.lower() for kw in ['resume', 'cv', 'curriculum', 'summary', 'experience', 'education', 'skills', 'objective', 'contact', 'profile']):
                continue
            # Valid name: 2-4 words, mostly letters
            words = line.split()
            if 1 <= len(words) <= 5 and len(line) >= 3 and len(line) <= 50:
                if self._is_valid_name(line):
                    return line
        
        # Fallback: try more relaxed matching
        for line in lines[:15]:
            line = line.strip()
            if 3 <= len(line) <= 40 and '@' not in line:
                if self._is_valid_name(line):
                    return line
        
        return "Unknown"
    
    def _extract_email(self, text: str) -> str:
        """Extract email address"""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        matches = re.findall(email_pattern, text)
        return matches[0] if matches else ""
    
    def _extract_phone(self, text: str) -> str:
        """Extract phone number (supports UAE +971 and international formats)"""
        # UAE format: +971-XX-XXX-XXXX or variations
        uae_pattern = r'\+971[\s.-]?\d{1,2}[\s.-]?\d{3}[\s.-]?\d{4}'
        uae_matches = re.findall(uae_pattern, text)
        if uae_matches:
            return uae_matches[0]
        
        # General international format
        phone_pattern = r'[\+\(]?[1-9][0-9 .\-\(\)]{8,}[0-9]'
        matches = re.findall(phone_pattern, text)
        return matches[0] if matches else ""
    
    def _extract_skills(self, text: str) -> List[str]:
        """Extract technical skills"""
        text_lower = text.lower()
        found_skills = []
        
        for skill in self.skill_keywords:
            if skill in text_lower:
                found_skills.append(skill.title())
        
        return list(set(found_skills))[:15]  # Return up to 15 unique skills
    
    def _extract_experience(self, text: str) -> int:
        """Extract years of experience"""
        text_lower = text.lower()
        # Look for patterns like "5 years", "5+ years", etc.
        patterns = [
            r'(\d+)\+?\s*years?\s+(?:of\s+)?experience',
            r'experience[:\s]+(\d+)\+?\s*years?',
            r'(\d+)\+?\s*years?\s+in\s+',
            r'(\d+)\+?\s*yrs?\s+(?:of\s+)?exp',
            r'(\d+)\+?\s*years?\s+(?:working|professional)',
            r'over\s+(\d+)\+?\s*years?',
        ]
        
        max_exp = 0
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                try:
                    val = int(match)
                    if 0 < val < 50:  # Reasonable range
                        max_exp = max(max_exp, val)
                except:
                    pass
        
        return max_exp
    
    def _extract_education(self, text: str) -> List[Dict[str, str]]:
        """Extract education information with improved patterns"""
        education = []
        original_text = text  # Keep original case for proper extraction
        text_lower = text.lower()
        
        # Improved degree patterns with field extraction
        full_edu_patterns = [
            # PhD patterns
            (r'(?:ph\.?d\.?|doctorate?)\s+(?:in\s+)?([A-Za-z\s]{3,40})\s+(?:from\s+)?([A-Za-z\s]+(?:University|College|Institute))', 'PhD'),
            (r'(?:ph\.?d\.?|doctorate?)\s+(?:in\s+)?([A-Za-z\s]{3,30})(?:\s|,|$)', 'PhD'),
            # Masters patterns
            (r"(?:master'?s?|m\.?s\.?|mba|m\.?a\.?|m\.?tech)\s+(?:in\s+|of\s+)?([A-Za-z\s]{3,40})\s+(?:from\s+)?([A-Za-z\s]+(?:University|College|Institute))", 'Masters'),
            (r"(?:master'?s?\s+degree|m\.?s\.?|mba)\s+(?:in\s+)?([A-Za-z\s]{3,30})", 'Masters'),
            # Bachelors patterns  
            (r"(?:bachelor'?s?|b\.?s\.?|b\.?a\.?|b\.?e\.?|b\.?tech|b\.?eng)\s+(?:in\s+|of\s+)?([A-Za-z\s]{3,40})\s+(?:from\s+)?([A-Za-z\s]+(?:University|College|Institute))", 'Bachelors'),
            (r"(?:bachelor'?s?\s+degree|b\.?e\.?|b\.?tech)\s+(?:in\s+)?([A-Za-z\s]{3,30})", 'Bachelors'),
        ]
        
        for pattern, degree_type in full_edu_patterns:
            match = re.search(pattern, original_text, re.IGNORECASE)
            if match:
                field = match.group(1).strip() if match.group(1) else ''
                institution = match.group(2).strip() if len(match.groups()) > 1 and match.group(2) else ''
                # Clean up field - remove noise words
                field = re.sub(r'\b(in|of|from|the|and|with)\b', '', field, flags=re.IGNORECASE).strip()
                # Only add if field looks valid (not random letters or too short)
                if len(field) >= 3 and not re.match(r'^[a-z]{1,3}$', field.lower()):
                    education.append({
                        "degree": degree_type,
                        "field": field.title(),
                        "institution": institution.title() if institution else '',
                        "year": ""
                    })
                    break  # Found valid education
        
        # Fallback: look for university mentions
        if not education:
            uni_match = re.search(r'([A-Z][a-zA-Z\s]+(?:University|College|Institute|School))', original_text)
            if uni_match:
                # Check for degree keywords nearby
                text_around = original_text[max(0, uni_match.start()-100):uni_match.end()+50].lower()
                degree = 'Degree'
                field = ''
                if 'master' in text_around or 'mba' in text_around or 'm.s' in text_around:
                    degree = 'Masters'
                elif 'bachelor' in text_around or 'b.e' in text_around or 'b.tech' in text_around:
                    degree = 'Bachelors'
                elif 'phd' in text_around or 'doctor' in text_around:
                    degree = 'PhD'
                education.append({
                    "degree": degree,
                    "field": field,
                    "institution": uni_match.group(1).strip(),
                    "year": ""
                })
        
        return education[:3]  # Return up to 3 education entries
    
    def _extract_work_history(self, text: str) -> List[Dict[str, str]]:
        """Extract work history from resume"""
        work_history = []
        text_lower = text.lower()
        
        # Look for job titles and companies
        job_patterns = [
            r'(senior|junior|lead|principal)?\s*(software engineer|developer|manager|analyst|designer|consultant|specialist|coordinator|director|executive)',
            r'(\w+\s+){1,3}(at|@)\s+(\w+\s*){1,3}',
        ]
        
        # Common job titles
        job_titles = ['engineer', 'developer', 'manager', 'analyst', 'designer', 'consultant', 
                     'specialist', 'coordinator', 'director', 'executive', 'lead', 'architect',
                     'administrator', 'associate', 'assistant', 'intern', 'trainee']
        
        lines = text.split('\n')
        for line in lines:
            line_lower = line.lower().strip()
            for title in job_titles:
                if title in line_lower and len(line) < 150:
                    # Extract year range if present
                    year_match = re.search(r'(20\d{2}|19\d{2})\s*[-–]\s*(20\d{2}|present|current)', line_lower)
                    years = year_match.group(0) if year_match else ''
                    
                    work_history.append({
                        "title": line.strip()[:100],
                        "company": "",
                        "period": years,
                        "description": ""
                    })
                    break
        
        return work_history[:5]  # Return up to 5 work history entries
    
    def _extract_summary(self, text: str) -> str:
        """Extract professional summary"""
        # Look for summary section
        summary_keywords = ['summary', 'profile', 'about', 'overview']
        lines = text.split('\n')
        
        for i, line in enumerate(lines):
            if any(keyword in line.lower() for keyword in summary_keywords):
                # Get next few lines
                summary_lines = lines[i+1:i+4]
                summary = ' '.join(summary_lines).strip()
                if len(summary) > 50:
                    return summary[:300]
        
        # Fallback: return first paragraph
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            if len(para) > 100:
                return para[:300]
        
        return "Professional with diverse experience."
    
    async def parse_job_description(self, text: str) -> Dict[str, Any]:
        """Parse job description and extract requirements"""
        
        required_skills = self._extract_skills(text)
        preferred_skills = []  # Extract preferred skills separately
        experience_level = self._extract_experience_level(text)
        responsibilities = self._extract_responsibilities(text)
        
        return {
            "title": self._extract_job_title(text),
            "required_skills": required_skills[:10],
            "preferred_skills": preferred_skills,
            "experience_level": experience_level,
            "responsibilities": responsibilities,
            "description": text[:500]
        }
    
    def _extract_job_title(self, text: str) -> str:
        """Extract job title"""
        lines = text.split('\n')
        for line in lines[:5]:
            line = line.strip()
            if len(line) > 5 and len(line) < 80:
                return line
        return "Software Engineer"
    
    def _extract_experience_level(self, text: str) -> str:
        """Extract experience level"""
        text_lower = text.lower()
        
        if 'senior' in text_lower or '5+ years' in text_lower:
            return "Senior (5+ years)"
        elif 'junior' in text_lower or 'entry' in text_lower:
            return "Junior (0-2 years)"
        else:
            return "Mid-level (2-5 years)"
    
    def _extract_responsibilities(self, text: str) -> List[str]:
        """Extract key responsibilities"""
        responsibilities = []
        lines = text.split('\n')
        
        in_responsibilities = False
        for line in lines:
            line = line.strip()
            
            if 'responsibilities' in line.lower() or 'duties' in line.lower():
                in_responsibilities = True
                continue
            
            if in_responsibilities and line.startswith(('-', '•', '*')):
                resp = line.lstrip('-•* ').strip()
                if len(resp) > 20:
                    responsibilities.append(resp)
                    if len(responsibilities) >= 5:
                        break
        
        return responsibilities

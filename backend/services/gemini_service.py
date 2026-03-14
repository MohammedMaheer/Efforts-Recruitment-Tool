"""
Google Gemini AI Service
========================
Primary AI engine for DEPLOYMENT (Cloud Run / GCP).
Uses Gemini 2.0 Flash for fast, cost-effective inference (~$0.10/1M input tokens).

Tier priority in production:
  1. Gemini (primary — fast, cheap, high quality)
  2. Keyword/embedding fallback (local_ai_service — no external dependency)
"""

import json
import logging
import re
import time
import asyncio
import hashlib
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Union

# ── Shared constants for pre-filter scoring (used by both chat() and rank_candidates_for_job()) ──

STOP_WORDS = frozenset({
    'find', 'me', 'the', 'a', 'an', 'is', 'are', 'in', 'for', 'and', 'or', 'with',
    'who', 'show', 'list', 'get', 'all', 'best', 'top', 'candidates', 'candidate',
    'can', 'you', 'i', 'we', 'our', 'have', 'has', 'do', 'does', 'what', 'how',
    'need', 'want', 'looking', 'search', 'tell', 'about', 'give', 'please',
    'any', 'some', 'good', 'from', 'to', 'of', 'that', 'this', 'be',
    'position', 'role', 'job', 'hiring', 'work', 'working', 'prefer', 'preferred',
    'should', 'must', 'minimum', 'experience', 'years', 'year', 'office',
    # Common verbs and noise words that don't carry search signal
    'worked', 'works', 'based', 'wanted', 'currently', 'previously',
    'between', 'company', 'companies', 'organization', 'organisations', 'organizations',
    'also', 'would', 'like', 'could', 'able', 'well', 'very', 'much', 'more',
    'within', 'at', 'their', 'them', 'they', 'he', 'she', 'his', 'her', 'it',
    'its', 'been', 'being', 'other', 'both', 'each', 'every', 'but', 'not',
    'only', 'than', 'just', 'most', 'those', 'such', 'will', 'one', 'two',
    'had', 'where', 'when', 'there', 'here', 'make', 'made', 'over', 'under',
    'around', 'through', 'during', 'after', 'before', 'while', 'already',
    'sector', 'field', 'domain', 'background', 'relevant', 'skills',
    'strong', 'knowledge', 'proficiency', 'expertise', 'familiar',
})

# ── Known job role titles — used to detect role intent in queries ──
# When these appear in a query, they indicate the user is searching for a specific role
ROLE_TITLES = frozenset({
    # Management
    'manager', 'director', 'head', 'lead', 'supervisor', 'coordinator', 'chief',
    'vice president', 'vp', 'cto', 'ceo', 'cfo', 'coo', 'cio', 'cmo',
    # Tech roles
    'developer', 'engineer', 'architect', 'programmer', 'analyst', 'administrator',
    'devops', 'sre', 'dba', 'data scientist', 'data engineer', 'data analyst',
    'frontend developer', 'backend developer', 'fullstack developer', 'full stack developer',
    'software engineer', 'web developer', 'mobile developer', 'cloud engineer',
    'machine learning engineer', 'ai engineer', 'qa engineer', 'test engineer',
    'security engineer', 'network engineer', 'systems engineer', 'platform engineer',
    # Business roles
    'accountant', 'consultant', 'specialist', 'executive', 'officer', 'associate',
    'representative', 'advisor', 'strategist', 'planner', 'controller',
    # Sales & Marketing
    'sales manager', 'account manager', 'business development manager', 'marketing manager',
    'sales executive', 'account executive', 'sales representative', 'relationship manager',
    'brand manager', 'product manager', 'growth manager', 'regional manager',
    # HR roles
    'recruiter', 'hr manager', 'hr executive', 'talent acquisition',
    # Design roles
    'designer', 'ui designer', 'ux designer', 'graphic designer', 'product designer',
    # Finance roles
    'auditor', 'financial analyst', 'treasury manager', 'risk analyst',
    # Operations
    'operations manager', 'project manager', 'program manager', 'delivery manager',
    'supply chain manager', 'logistics manager', 'procurement manager', 'warehouse manager',
    # Healthcare
    'nurse', 'doctor', 'physician', 'pharmacist', 'therapist', 'technician',
    # Education
    'teacher', 'professor', 'instructor', 'trainer', 'tutor', 'lecturer',
    # General
    'intern', 'trainee', 'assistant', 'secretary', 'receptionist', 'clerk',
    'driver', 'chef', 'electrician', 'plumber', 'mechanic', 'welder',
})

# ── Industry/domain keywords — detect when query asks for a specific industry ──
INDUSTRY_KEYWORDS = {
    'it': {'information technology', 'software', 'technology', 'tech', 'it services', 'it company', 'it sector'},
    'it services': {'information technology', 'software', 'technology services', 'managed services'},
    'banking': {'bank', 'financial services', 'fintech', 'finance', 'nbfc'},
    'fintech': {'financial technology', 'banking', 'payments', 'digital banking'},
    'healthcare': {'hospital', 'medical', 'pharma', 'pharmaceutical', 'clinical', 'health'},
    'pharmaceutical': {'pharma', 'drug', 'biotech', 'life sciences'},
    'manufacturing': {'factory', 'production', 'industrial', 'plant', 'assembly'},
    'retail': {'e-commerce', 'ecommerce', 'store', 'fmcg', 'consumer goods'},
    'ecommerce': {'e-commerce', 'online retail', 'marketplace', 'digital commerce'},
    'telecom': {'telecommunications', 'telco', 'mobile', 'network operator'},
    'consulting': {'consultancy', 'advisory', 'management consulting', 'professional services'},
    'construction': {'building', 'civil engineering', 'infrastructure', 'real estate'},
    'real estate': {'property', 'realty', 'construction', 'development'},
    'education': {'university', 'school', 'college', 'academic', 'training'},
    'media': {'advertising', 'entertainment', 'publishing', 'digital media'},
    'automotive': {'automobile', 'vehicle', 'car', 'ev', 'electric vehicle'},
    'logistics': {'supply chain', 'transportation', 'freight', 'shipping', 'warehousing'},
    'insurance': {'underwriting', 'actuarial', 'claims', 'reinsurance'},
    'oil and gas': {'petroleum', 'energy', 'upstream', 'downstream', 'refinery'},
    'energy': {'oil and gas', 'renewable', 'solar', 'wind', 'power'},
    'hospitality': {'hotel', 'restaurant', 'tourism', 'catering', 'food service'},
    'government': {'public sector', 'civil service', 'municipal'},
    'ngo': {'non-profit', 'nonprofit', 'social enterprise', 'foundation'},
    'startup': {'start-up', 'early stage', 'seed stage', 'growth stage'},
    'bpo': {'call center', 'outsourcing', 'ites', 'contact center'},
    'aviation': {'airline', 'airport', 'aerospace', 'flight'},
}

LOCATION_ALIASES = {
    # Country / region aliases
    'uae': ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'fujairah', 'ras al khaimah', 'umm al quwain', 'united arab emirates'],
    'usa': ['united states', 'new york', 'california', 'texas', 'florida', 'chicago', 'los angeles', 'san francisco', 'seattle', 'boston', 'austin'],
    'uk': ['united kingdom', 'london', 'manchester', 'birmingham', 'england', 'scotland'],
    'india': ['mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune', 'kolkata', 'noida', 'gurgaon', 'gurugram', 'jaipur', 'ahmedabad', 'kochi', 'cochin', 'coimbatore', 'thiruvananthapuram', 'trivandrum'],
    'gcc': ['saudi arabia', 'kuwait', 'bahrain', 'oman', 'qatar', 'dubai', 'abu dhabi', 'riyadh', 'doha', 'muscat'],
    'ksa': ['saudi arabia', 'riyadh', 'jeddah', 'dammam', 'mecca', 'medina'],
    # Indian city <-> state bidirectional aliases
    'chennai': ['tamil nadu', 'tamilnadu', 'tn', 'madras'],
    'tamil nadu': ['chennai', 'coimbatore', 'madurai', 'trichy', 'salem', 'tamilnadu'],
    'tamilnadu': ['chennai', 'tamil nadu', 'coimbatore', 'madurai'],
    'bangalore': ['bengaluru', 'karnataka'],
    'bengaluru': ['bangalore', 'karnataka'],
    'karnataka': ['bangalore', 'bengaluru', 'mysore', 'mysuru', 'mangalore'],
    'hyderabad': ['telangana', 'andhra pradesh', 'secunderabad'],
    'telangana': ['hyderabad', 'secunderabad', 'warangal'],
    'mumbai': ['maharashtra', 'bombay', 'navi mumbai', 'thane'],
    'maharashtra': ['mumbai', 'pune', 'nagpur', 'nashik', 'thane'],
    'delhi': ['new delhi', 'ncr', 'noida', 'gurgaon', 'gurugram', 'faridabad', 'ghaziabad'],
    'ncr': ['delhi', 'new delhi', 'noida', 'gurgaon', 'gurugram', 'faridabad', 'ghaziabad'],
    'noida': ['delhi', 'ncr', 'uttar pradesh'],
    'gurgaon': ['gurugram', 'delhi', 'ncr', 'haryana'],
    'gurugram': ['gurgaon', 'delhi', 'ncr', 'haryana'],
    'pune': ['maharashtra'],
    'kolkata': ['west bengal', 'calcutta'],
    'kochi': ['cochin', 'kerala', 'ernakulam'],
    'cochin': ['kochi', 'kerala'],
    'kerala': ['kochi', 'cochin', 'thiruvananthapuram', 'trivandrum', 'kozhikode', 'calicut'],
    'dubai': ['uae', 'united arab emirates'],
    'abu dhabi': ['uae', 'united arab emirates'],
    'sharjah': ['uae', 'united arab emirates'],
    'riyadh': ['ksa', 'saudi arabia'],
    'jeddah': ['ksa', 'saudi arabia'],
    # East Asia / SEA
    'singapore': ['sg'],
    'malaysia': ['kuala lumpur', 'kl'],
}

# Comprehensive skill synonyms for flexible matching
SKILL_SYNONYMS = {
    # AI / ML
    'ml': {'machine learning', 'machine', 'learning'},
    'machine learning': {'ml', 'deep learning', 'neural networks'},
    'ai': {'artificial intelligence', 'machine learning', 'ml', 'deep learning'},
    'artificial intelligence': {'ai', 'ml'},
    'nlp': {'natural language processing', 'text mining', 'text analytics'},
    'natural language processing': {'nlp'},
    'deep learning': {'dl', 'neural networks', 'tensorflow', 'pytorch'},
    'computer vision': {'cv', 'image processing', 'opencv'},
    # RPA / Automation
    'rpa': {'robotic process automation', 'uipath', 'blueprism', 'blue prism', 'automation anywhere', 'power automate'},
    'robotic process automation': {'rpa'},
    'uipath': {'rpa', 'robotic process automation'},
    'blueprism': {'rpa', 'blue prism'},
    'blue prism': {'rpa', 'blueprism'},
    'automation anywhere': {'rpa'},
    'power automate': {'microsoft power automate', 'power platform', 'rpa'},
    'microsoft power automate': {'power automate', 'power platform'},
    'power platform': {'power automate', 'power apps', 'power bi'},
    # JavaScript ecosystem
    'react': {'reactjs', 'react.js', 'react js'},
    'reactjs': {'react', 'react.js'},
    'react.js': {'react', 'reactjs'},
    'angular': {'angularjs', 'angular.js', 'angular js'},
    'angularjs': {'angular', 'angular.js'},
    'vue': {'vuejs', 'vue.js', 'vue js'},
    'vuejs': {'vue', 'vue.js'},
    'next': {'nextjs', 'next.js'},
    'nextjs': {'next', 'next.js'},
    'next.js': {'next', 'nextjs'},
    'node': {'nodejs', 'node.js', 'node js'},
    'nodejs': {'node', 'node.js'},
    'node.js': {'node', 'nodejs'},
    'express': {'expressjs', 'express.js'},
    'js': {'javascript'},
    'javascript': {'js', 'ecmascript', 'es6'},
    'ts': {'typescript'},
    'typescript': {'ts'},
    'jquery': {'jquery'},
    # Python ecosystem
    'python': {'py', 'python3'},
    'django': {'django rest framework', 'drf'},
    'flask': {'flask'},
    'fastapi': {'fast api'},
    # Java / JVM
    'java': {'j2ee', 'jee', 'jdk'},
    'spring': {'spring boot', 'springboot', 'spring framework'},
    'spring boot': {'spring', 'springboot'},
    'springboot': {'spring', 'spring boot'},
    'kotlin': {'kt'},
    'scala': {'scala'},
    # .NET / C#
    'c#': {'csharp', 'c sharp', '.net', 'dotnet'},
    'csharp': {'c#', '.net'},
    '.net': {'dotnet', 'c#', 'csharp', 'asp.net'},
    'dotnet': {'.net', 'c#'},
    'asp.net': {'.net', 'dotnet', 'c#'},
    # C / C++
    'c++': {'cpp', 'c plus plus', 'cplusplus'},
    'cpp': {'c++', 'c plus plus'},
    # DevOps / Cloud
    'devops': {'ci/cd', 'cicd', 'docker', 'kubernetes', 'jenkins', 'terraform', 'ansible'},
    'cicd': {'ci/cd', 'continuous integration', 'continuous deployment', 'devops'},
    'ci/cd': {'cicd', 'continuous integration', 'devops'},
    'docker': {'container', 'containerization'},
    'kubernetes': {'k8s', 'container orchestration'},
    'k8s': {'kubernetes'},
    'terraform': {'tf', 'infrastructure as code', 'iac'},
    'ansible': {'configuration management'},
    'aws': {'amazon web services', 'cloud', 'ec2', 's3', 'lambda'},
    'amazon web services': {'aws'},
    'azure': {'microsoft azure', 'cloud'},
    'microsoft azure': {'azure'},
    'gcp': {'google cloud', 'google cloud platform', 'cloud'},
    'google cloud': {'gcp', 'google cloud platform'},
    'cloud': {'aws', 'azure', 'gcp'},
    # Databases
    'sql': {'mysql', 'postgresql', 'oracle', 'database', 'rdbms', 'mssql', 'sql server'},
    'mysql': {'sql', 'database', 'rdbms'},
    'postgresql': {'postgres', 'sql', 'database'},
    'postgres': {'postgresql'},
    'mongodb': {'mongo', 'nosql'},
    'mongo': {'mongodb', 'nosql'},
    'nosql': {'mongodb', 'cassandra', 'redis', 'dynamodb'},
    'oracle': {'oracle db', 'plsql', 'pl/sql'},
    'sql server': {'mssql', 'microsoft sql server', 'tsql'},
    'mssql': {'sql server', 'microsoft sql server'},
    'redis': {'cache', 'in-memory database'},
    'database': {'sql', 'nosql', 'rdbms', 'dbms'},
    # Full-stack
    'fullstack': {'full stack', 'full-stack', 'frontend', 'backend'},
    'full stack': {'fullstack', 'full-stack'},
    'frontend': {'front-end', 'front end', 'ui', 'client-side'},
    'backend': {'back-end', 'back end', 'server-side', 'api'},
    # Data
    'data science': {'data scientist', 'analytics', 'machine learning'},
    'data engineering': {'data engineer', 'etl', 'data pipeline'},
    'data analytics': {'data analysis', 'analytics', 'business intelligence', 'bi'},
    'power bi': {'powerbi', 'business intelligence', 'bi'},
    'powerbi': {'power bi'},
    'tableau': {'data visualization', 'business intelligence'},
    'etl': {'data pipeline', 'data engineering'},
    # QA / Testing
    'qa': {'testing', 'quality assurance', 'test automation', 'selenium', 'qc'},
    'testing': {'qa', 'quality assurance', 'test'},
    'selenium': {'test automation', 'qa', 'web testing'},
    'automation testing': {'test automation', 'qa', 'selenium', 'cypress'},
    'test automation': {'automation testing', 'qa', 'selenium'},
    'manual testing': {'qa', 'quality assurance'},
    # Security
    'cyber': {'cybersecurity', 'security', 'infosec'},
    'cybersecurity': {'cyber security', 'security', 'infosec', 'soc', 'siem'},
    'security': {'cybersecurity', 'infosec'},
    'infosec': {'information security', 'cybersecurity'},
    # ERP / Business
    'sap': {'sap hana', 'sap s/4hana', 'erp'},
    'erp': {'sap', 'oracle erp', 'dynamics 365'},
    'salesforce': {'sfdc', 'crm'},
    'crm': {'salesforce', 'hubspot', 'dynamics crm'},
    # Project Management
    'scrum': {'agile', 'sprint', 'kanban', 'scrum master'},
    'agile': {'scrum', 'sprint', 'kanban'},
    'pmp': {'project management', 'project manager'},
    'pm': {'project management', 'project manager'},
    'project management': {'pm', 'pmp', 'agile', 'scrum'},
    # HR / Recruitment
    'hr': {'human resources', 'recruitment', 'talent acquisition', 'hrbp', 'people operations'},
    'recruitment': {'talent acquisition', 'hiring', 'hr', 'staffing', 'sourcing'},
    'talent acquisition': {'recruitment', 'hiring', 'sourcing'},
    'hrbp': {'hr business partner', 'human resources', 'hr'},
    # Design
    'ui/ux': {'ux', 'ui', 'user experience', 'user interface', 'ux design', 'ui design'},
    'ux': {'user experience', 'ui/ux', 'ux design', 'ux research'},
    'ui': {'user interface', 'ui/ux', 'ui design'},
    'figma': {'ui design', 'ux design', 'prototyping', 'sketch'},
    # Mobile
    'ios': {'swift', 'objective-c', 'xcode', 'apple'},
    'android': {'kotlin', 'java', 'mobile'},
    'react native': {'mobile', 'cross-platform', 'react'},
    'flutter': {'dart', 'mobile', 'cross-platform'},
    # API / Integration
    'rest': {'rest api', 'restful', 'api'},
    'rest api': {'restful', 'rest', 'api'},
    'restful': {'rest api', 'rest'},
    'graphql': {'graph ql', 'api'},
    'api': {'rest', 'restful', 'graphql', 'web services'},
    'microservices': {'micro services', 'distributed systems'},
    'micro services': {'microservices'},
    # Networking / Infra
    'networking': {'network', 'ccna', 'ccnp', 'cisco'},
    'cisco': {'ccna', 'ccnp', 'networking'},
    'linux': {'unix', 'ubuntu', 'centos', 'rhel'},
    'unix': {'linux'},
    # Automation general
    'automate': {'automation', 'scripting'},
    'automation': {'automate', 'scripting', 'rpa'},
    # ── Finance / Accounting ──
    'finance': {'financial analysis', 'financial modeling', 'accounting', 'treasury', 'budgeting'},
    'accounting': {'accounts', 'bookkeeping', 'financial reporting', 'audit', 'cpa', 'acca'},
    'cpa': {'certified public accountant', 'accounting'},
    'acca': {'chartered accountant', 'accounting'},
    'cfa': {'chartered financial analyst', 'finance', 'investment'},
    'audit': {'auditing', 'internal audit', 'external audit', 'accounting'},
    'treasury': {'cash management', 'finance'},
    'tax': {'taxation', 'tax planning', 'vat', 'corporate tax'},
    # ── Sales / Business Development ──
    'sales': {'business development', 'account management', 'revenue', 'selling'},
    'business development': {'bd', 'sales', 'partnerships'},
    'account management': {'key accounts', 'client management', 'sales'},
    'b2b': {'business to business', 'enterprise sales'},
    'b2c': {'business to consumer', 'retail sales', 'consumer'},
    # ── Marketing ──
    'marketing': {'digital marketing', 'brand management', 'advertising', 'seo', 'sem'},
    'digital marketing': {'seo', 'sem', 'social media marketing', 'ppc', 'google ads'},
    'seo': {'search engine optimization', 'digital marketing'},
    'sem': {'search engine marketing', 'ppc', 'google ads'},
    'content marketing': {'content writing', 'copywriting', 'blog'},
    'social media': {'social media marketing', 'smm', 'community management'},
    # ── Operations / Supply Chain ──
    'operations': {'operations management', 'process improvement', 'lean', 'six sigma'},
    'supply chain': {'scm', 'logistics', 'procurement', 'warehousing'},
    'logistics': {'supply chain', 'freight', 'shipping', 'transportation'},
    'procurement': {'purchasing', 'sourcing', 'vendor management'},
    'lean': {'lean manufacturing', 'six sigma', 'kaizen', 'continuous improvement'},
    'six sigma': {'lean', 'process improvement', 'quality'},
    # ── Healthcare ──
    'healthcare': {'medical', 'hospital', 'clinical', 'health'},
    'nursing': {'nurse', 'registered nurse', 'rn', 'healthcare'},
    'pharmacy': {'pharmacist', 'pharmaceutical', 'pharma'},
    # ── Legal ──
    'legal': {'law', 'lawyer', 'attorney', 'compliance', 'contracts'},
    'compliance': {'regulatory', 'governance', 'risk', 'legal'},
    # ── Consulting ──
    'consulting': {'consultant', 'advisory', 'management consulting', 'strategy'},
    # ── Education ──
    'education': {'teaching', 'training', 'e-learning', 'curriculum'},
    'teaching': {'teacher', 'instructor', 'professor', 'education'},
    # ── Insurance ──
    'insurance': {'underwriting', 'claims', 'risk management', 'actuarial'},
    # ── Real Estate ──
    'real estate': {'property', 'realty', 'property management', 'leasing'},
    # ── IT / Technology Industry ──
    'it': {'information technology', 'it services', 'technology', 'software', 'tech'},
    'it services': {'it', 'information technology', 'technology services', 'managed services'},
    'information technology': {'it', 'it services', 'tech'},
    'ites': {'it enabled services', 'bpo', 'call center', 'outsourcing'},
}

# All known location names for direct detection (fallback when regex misses)
KNOWN_LOCATIONS = frozenset({
    # UAE
    'dubai', 'abu dhabi', 'sharjah', 'ajman', 'fujairah', 'ras al khaimah', 'uae',
    # KSA
    'riyadh', 'jeddah', 'dammam', 'mecca', 'medina', 'ksa', 'saudi arabia',
    # GCC
    'bahrain', 'kuwait', 'oman', 'qatar', 'doha', 'muscat', 'manama', 'gcc',
    # India
    'mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune',
    'kolkata', 'noida', 'gurgaon', 'gurugram', 'kochi', 'cochin', 'jaipur',
    'ahmedabad', 'coimbatore', 'lucknow', 'chandigarh', 'indore', 'nagpur',
    'thiruvananthapuram', 'trivandrum', 'bhopal', 'visakhapatnam', 'kerala',
    'tamil nadu', 'karnataka', 'maharashtra', 'telangana', 'india',
    # USA
    'new york', 'california', 'texas', 'florida', 'chicago', 'los angeles',
    'san francisco', 'seattle', 'boston', 'austin', 'denver', 'atlanta',
    'houston', 'dallas', 'philadelphia', 'washington', 'usa', 'united states',
    # UK / Europe
    'london', 'manchester', 'birmingham', 'uk', 'united kingdom', 'england',
    'germany', 'berlin', 'munich', 'france', 'paris', 'netherlands', 'amsterdam',
    'ireland', 'dublin', 'spain', 'barcelona', 'madrid', 'italy', 'portugal',
    # Asia-Pacific
    'singapore', 'malaysia', 'kuala lumpur', 'hong kong', 'japan', 'tokyo',
    'australia', 'sydney', 'melbourne', 'canada', 'toronto', 'vancouver',
    # Africa
    'south africa', 'johannesburg', 'cape town', 'nigeria', 'lagos', 'kenya', 'nairobi',
    'egypt', 'cairo',
})

# Pre-compiled regex for location detection — handles diverse prompt styles
LOCATION_PATTERN = re.compile(
    r'(?:'
    r'(?:based|located?|residing|living|settled)\s+(?:in|at|near)'
    r'|work\s*(?:ing)?\s*(?:from|in|at)'
    r'|(?:prefer(?:red|ably)?|must\s+be|should\s+be|needs?\s+to\s+be)\s+(?:in|from|at|near|to\s+work\s+(?:from|in))'
    r'|office\s+in'
    r'|(?:primary|secondary|acceptable)\s*(?:location)?\s*:?\s*'
    r'|location\s*:?\s*'
    r'|candidates?\s+(?:from|in|at|near)'
    r'|(?:from|in|at|near|within)\s+'
    r')'
    r'\s*([A-Za-z][A-Za-z\s,]{1,50}?)'
    r'(?:\s*[.\-;]|\s+(?:office|with|who|minimum|min|experience|exp|having|'
    r'and|must|should|can|preferr?ed?|at\s+least|only|secondary|primary|\d)|$)',
    re.IGNORECASE
)

# Negative/exclusion pattern — detect what to exclude from results
NEGATIVE_PATTERN = re.compile(
    r'(?:exclude|not?|without|no|remove|skip|ignore|avoid|except|excluding|other\s+than)'
    r'\s+(?:candidates?\s+(?:from|in|with|who)\s+)?'
    r'(.+?)(?:\s*[.;,]|\s+(?:and|but|only|candidates?|from|experience)|$)',
    re.IGNORECASE
)

# Seniority level pattern — detect required seniority beyond just experience years
SENIORITY_PATTERN = re.compile(
    r'\b(junior|mid[\s-]?level|senior|lead|principal|staff|director|head|vp|c[\s-]?level|chief|manager|executive|intern|trainee|fresher|fresh\s*graduate|entry[\s-]?level)\b',
    re.IGNORECASE
)

# Nationality/visa preference detection
NATIONALITY_PATTERN = re.compile(
    r'(?:nationality|passport|citizen(?:ship)?|visa|work\s*(?:permit|authorization)|national)'
    r'\s*:?\s*([A-Za-z\s,]{2,40})',
    re.IGNORECASE
)

# Pre-compiled regex for minimum experience detection
EXPERIENCE_PATTERN = re.compile(
    r'(?:minimum|min|at\s+least|above|more\s+than|over)?\s*(\d+)\+?\s*(?:years?|yrs?|y)\s*(?:of\s+)?(?:experience|exp|work)?',
    re.IGNORECASE
)

# Freshers / zero experience pattern
FRESHER_PATTERN = re.compile(
    r'\b(?:freshers?|fresh\s*graduates?|fresh\s*grads?|no\s+experience|zero\s+experience|0\s*(?:years?|yrs?)\s*(?:experience|exp)?|entry[\s-]?level|interns?(?:ship)?|trainees?|beginners?|new\s+graduates?|recent\s+graduates?)s?\b',
    re.IGNORECASE
)

# Experience RANGE pattern — comprehensive: handles ranges, caps, exclusions, and natural language
EXPERIENCE_RANGE_PATTERN = re.compile(
    r'(?:'
    r'(\d+)\s*(?:to|-|–|\-)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|between\s*(\d+)\s*(?:years?|yrs?)?\s*(?:and|&|to)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:experience|exp)\s+(?:of\s+)?(?:from\s+)?(\d+)\s*(?:years?|yrs?)?\s*(?:to|-|–|and|&)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:no\s+more\s+than|not\s+more\s+than|max(?:imum)?|under|below|less\s+than|at\s+most|upto|up\s+to|within)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:do\s+not|don\'t|doesn\'t|should\s+not|must\s+not|cannot|can\'t)\s+(?:include|have|exceed|be|contain|show).*?(?:more\s+than|above|over|exceed(?:ing)?)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:must\s+not|should\s+not|cannot|can\'t)\s+exceed\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|experience\s*(?:of\s+)?(?:no\s+more\s+than|less\s+than|under|below|max(?:imum)?)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:only|strictly)\s+(\d+)\s*(?:to|-|–)\s*(\d+)\s*(?:years?|yrs?|y)'
    r')',
    re.IGNORECASE
)

# Count extraction from message: "show me 15 candidates", "top 20", "list 25"
COUNT_PATTERN = re.compile(
    r'(?:show|give|list|find|get|display|return|fetch|bring|send|provide)\s+(?:me\s+)?(?:the\s+)?(?:top\s+)?(\d+)'
    r'|(?:top|best|first)\s+(\d+)'
    r'|(\d+)\s+(?:candidates|results|people|profiles|matches|applicants|resumes|cvs)',
    re.IGNORECASE
)

# Shared precompiled patterns used in post-processing AI output
_CID_RE = re.compile(r'\(cid:\d+\)')
_DIGITS_ONLY_RE = re.compile(r'\D')

# Query normalization patterns — precompiled once at module load for _normalize_query_for_cache
_QN_FILLER = [
    re.compile(r'^(?:please\s+)?(?:can\s+you\s+)?(?:could\s+you\s+)?', re.IGNORECASE),
    re.compile(r'^(?:show\s+me\s+|give\s+me\s+|find\s+me\s+|list\s+|get\s+me\s+)', re.IGNORECASE),
    re.compile(r'^(?:i\s+need\s+|i\s+want\s+|i\'m\s+looking\s+for\s+)', re.IGNORECASE),
]
_QN_SYNONYMS = [
    (re.compile(r'\bdevelopers?\b', re.IGNORECASE), 'developer'),
    (re.compile(r'\bengineers?\b', re.IGNORECASE), 'engineer'),
    (re.compile(r'\banalysts?\b', re.IGNORECASE), 'analyst'),
    (re.compile(r'\bmanagers?\b', re.IGNORECASE), 'manager'),
    (re.compile(r'\bspecialists?\b', re.IGNORECASE), 'specialist'),
]
_QN_WHITESPACE = re.compile(r'\s+')


def _expand_location_terms(raw_terms: list, stop_words: frozenset = STOP_WORDS) -> set:
    """Expand location terms using aliases (bidirectional). Returns set of all matching terms."""
    expanded: set = set()
    for term in raw_terms:
        expanded.add(term)
        for word in term.split():
            if len(word) > 2:
                expanded.add(word)
        if term in LOCATION_ALIASES:
            for alias in LOCATION_ALIASES[term]:
                expanded.add(alias)
        for alias_key, alias_vals in LOCATION_ALIASES.items():
            if term in alias_vals or term == alias_key:
                expanded.add(alias_key)
                for v in alias_vals:
                    expanded.add(v)
    return expanded


def _extract_location_from_text(text: str) -> list:
    """Extract required location terms from a query or JD using regex + direct city detection.
    
    Uses a multi-pass approach:
    1. Direct known-location detection with OR/AND splitting (most reliable)
    2. Regex-based extraction as supplement (handles "based in Dubai", "from Chennai", etc.)
    3. Validates all regex results against KNOWN_LOCATIONS to avoid false positives
    """
    terms = []
    text_lower = text.lower()
    
    # Pass 1: Direct known-location detection (highest priority — avoids regex false positives)
    # Split text into words and multi-word tokens, check against KNOWN_LOCATIONS
    # Also handle "uae or india", "dubai and abu dhabi", "chennai, mumbai"
    
    # Check multi-word locations first (e.g. "abu dhabi", "new york", "san francisco")
    found_multi = set()
    for loc in sorted(KNOWN_LOCATIONS, key=len, reverse=True):
        if ' ' in loc and loc in text_lower:
            found_multi.add(loc)
    
    # Check single-word locations
    text_words = re.sub(r'[^\w\s]', ' ', text_lower).split()
    found_single = set()
    for loc in KNOWN_LOCATIONS:
        if ' ' not in loc and loc in text_words:
            # Avoid matching "it" as a location (it's not in KNOWN_LOCATIONS, but be safe)
            if len(loc) >= 2:
                found_single.add(loc)
    
    found_all = found_multi | found_single
    
    if found_all:
        terms = list(found_all)
        return terms
    
    # Pass 2: Regex-based extraction (fallback for unusual phrasings)
    match = LOCATION_PATTERN.search(text)
    if match:
        raw_loc = match.group(1).strip().strip(',').strip()
        # Split on commas and or/and separators
        parts = re.split(r'\s*[,]\s*|\s+(?:or|and|&)\s+', raw_loc)
        for part in parts:
            part = part.strip().lower()
            if part and part not in STOP_WORDS and len(part) > 1:
                # Validate against KNOWN_LOCATIONS to avoid false positives
                if part in KNOWN_LOCATIONS:
                    terms.append(part)
                else:
                    # Check if part contains a known location
                    for loc in KNOWN_LOCATIONS:
                        if loc in part or part in loc:
                            terms.append(loc)
                            break
    
    return terms


def _safe_int_experience(val) -> int:
    """Safely convert experience to int, handling strings like '5+', '3-5', and None."""
    try:
        if isinstance(val, str):
            val = val.replace('+', '').replace('years', '').replace('yrs', '').strip()
            if '-' in val:
                parts = val.split('-')
                val = parts[0].strip()  # use lower bound
        return int(float(val or 0))
    except (ValueError, TypeError):
        return 0

logger = logging.getLogger(__name__)

# Try to import google-generativeai
try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    genai_types = None  # type: ignore
    GEMINI_AVAILABLE = False
    logger.info("google-genai not installed — Gemini service disabled")


def _repair_json(text: str) -> Optional[Dict]:
    """Lightweight JSON repair for Gemini output. Always returns a dict or None."""
    if not text:
        return None
    # Strip markdown fences
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```$', '', text).strip()

    def _ensure_dict(val):
        """Ensure the result is a dict, not a list or other type."""
        if isinstance(val, dict):
            return val
        if isinstance(val, list):
            # Return first dict in list, or None
            for item in val:
                if isinstance(item, dict):
                    return item
            return None
        return None

    # Direct parse
    try:
        parsed = json.loads(text)
        result = _ensure_dict(parsed)
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass

    # Extract outermost balanced {...} JSON object (balanced brace finder)
    def _find_balanced_json(t: str) -> Optional[str]:
        start = t.find('{')
        if start == -1:
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return t[start:i+1]
        return None

    candidate = _find_balanced_json(text)
    if not candidate:
        _m = re.search(r'\{[\s\S]*\}', text)
        candidate = _m.group() if _m else None
    if candidate:
        try:
            parsed = json.loads(candidate)
            result = _ensure_dict(parsed)
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass
        # Fix trailing commas
        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            parsed = json.loads(fixed)
            result = _ensure_dict(parsed)
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass

    # Patch truncated JSON
    open_b = text.count('{') - text.count('}')
    open_k = text.count('[') - text.count(']')
    if open_b > 0 or open_k > 0:
        patched = text + (']' * max(open_k, 0)) + ('}' * max(open_b, 0))
        patched = re.sub(r',\s*([}\]])', r'\1', patched)
        try:
            parsed = json.loads(patched)
            result = _ensure_dict(parsed)
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass

    logger.debug(f"_repair_json: all strategies failed for text ({len(text)} chars): {text[:200]}")
    return None


class GeminiService:
    """
    Google Gemini AI Service — mirrors the interface of LLMService
    so it can be used as a drop-in replacement in the AI tier chain.

    Capabilities:
    - Resume parsing (structured JSON extraction)
    - Email candidate extraction
    - Deep candidate analysis (pros/cons/recommendation)
    - Candidate–job matching with detailed scoring
    - Batch candidate ranking
    - Candidate comparison
    - AI chat assistant
    - Interview question generation
    - Job description parsing
    - Email template generation
    """

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.5-flash"):
        import os
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model_name = model_name
        self.available = False
        self._client = None

        # Performance tracking
        self._request_count = 0
        self._total_time = 0.0
        self._error_count = 0

        # Thread-safe response cache (OrderedDict for LRU eviction)
        self._cache: OrderedDict = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_max_size = 500
        self._cache_ttl = 14400  # 4 hours (structured extraction rarely changes)

        # Thread-safe search cache
        self._search_cache: OrderedDict = OrderedDict()
        self._search_cache_lock = threading.Lock()
        self._search_cache_ttl = 900  # 15 minutes

        # Thread-safe daily budget tracking — prevents runaway costs
        self._budget_lock = threading.Lock()
        self._daily_call_count = 0
        self._daily_call_date = ''  # YYYY-MM-DD
        self._daily_call_limit = int(os.environ.get('GEMINI_DAILY_LIMIT', '2000'))  # Max API calls/day

        if not GEMINI_AVAILABLE:
            logger.warning("⚠️ google-genai package not installed. Run: pip install google-genai")
            return

        if not self.api_key:
            logger.warning("⚠️ GEMINI_API_KEY not set — Gemini service disabled")
            return

        try:
            self._client = genai.Client(api_key=self.api_key)
            self.available = True
            logger.info(f"✅ Gemini AI initialized: {self.model_name}")
        except Exception as e:
            logger.error(f"❌ Gemini initialization failed: {e}")
            self.available = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()
        return f"gemini:{prefix}:{h}"

    def _get_cached(self, key: str) -> Optional[Any]:
        with self._cache_lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() - entry['time'] < self._cache_ttl:
                    self._cache.move_to_end(key)  # LRU: mark as recently used
                    return entry['data']
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any):
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = {'data': data, 'time': time.time()}
            else:
                if len(self._cache) >= self._cache_max_size:
                    # Evict oldest 20% (LRU) — O(1) per pop from OrderedDict
                    for _ in range(self._cache_max_size // 5):
                        if self._cache:
                            self._cache.popitem(last=False)
                self._cache[key] = {'data': data, 'time': time.time()}

    def _get_search_cached(self, query: str, num_candidates: int, total: int) -> Optional[Any]:
        """Check search cache using normalized query key. Thread-safe."""
        normalized = self._normalize_query_for_cache(query)
        key = f"search:{hashlib.sha256(f'{normalized}:{num_candidates}'.encode()).hexdigest()}"
        with self._search_cache_lock:
            if key in self._search_cache:
                entry = self._search_cache[key]
                ttl = entry.get('ttl', self._search_cache_ttl)
                if time.time() - entry['time'] < ttl:
                    self._search_cache.move_to_end(key)
                    logger.info(f"Search cache HIT for: {query[:60]}")
                    return entry['data']
                del self._search_cache[key]
        return None

    def _set_search_cache(self, query: str, num_candidates: int, total: int, data: Any):
        """Cache search result with adaptive TTL. Thread-safe.
        - Rich results (many candidates returned): 15 min TTL
        - Thin results (few/no candidates): 5 min TTL (pool may grow soon)
        """
        normalized = self._normalize_query_for_cache(query)
        key = f"search:{hashlib.sha256(f'{normalized}:{num_candidates}'.encode()).hexdigest()}"
        # Adaptive TTL: fewer results → shorter cache (candidate pool might grow)
        result_count = num_candidates if isinstance(num_candidates, int) else 0
        ttl = self._search_cache_ttl if result_count >= 5 else max(300, self._search_cache_ttl // 3)
        with self._search_cache_lock:
            if len(self._search_cache) >= 50:
                for _ in range(20):
                    if self._search_cache:
                        self._search_cache.popitem(last=False)
            self._search_cache[key] = {'data': data, 'time': time.time(), 'ttl': ttl}

    @staticmethod
    def _normalize_query_for_cache(query: str) -> str:
        """Normalize query for better cache hit rate.
        Strips filler phrases and normalizes whitespace/case so semantically
        identical queries ("show me python devs" vs "list python developers") share a cache entry."""
        q = query.lower().strip()
        for pattern in _QN_FILLER:
            q = pattern.sub('', q)
        for pattern, replacement in _QN_SYNONYMS:
            q = pattern.sub(replacement, q)
        return _QN_WHITESPACE.sub(' ', q).strip()

    def _generate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024, thinking_budget: int = 0) -> str:
        """Synchronous text generation via Gemini.
        
        thinking_budget: Controls Gemini 2.5 Flash's internal reasoning tokens.
          0 = disabled (cheap — use for JSON extraction / structured tasks)
          >0 = enabled with token cap (use for open-ended chat / analysis)
        Thinking tokens cost $3.50/1M vs $0.60/1M for output — disable when not needed.
        """
        if not self.available or not self._client:
            return ""
        # ── Thread-safe daily budget check ──
        import datetime as _dt
        today = _dt.date.today().isoformat()
        with self._budget_lock:
            if self._daily_call_date != today:
                self._daily_call_date = today
                self._daily_call_count = 0
            if self._daily_call_count >= self._daily_call_limit:
                logger.warning(f"Gemini daily limit reached ({self._daily_call_limit} calls). Skipping API call.")
                raise RuntimeError(f"Gemini daily call limit reached ({self._daily_call_limit})")
            self._daily_call_count += 1
        start = time.time()
        try:
            # Build config — disable thinking for extraction tasks to save ~70% cost
            config_kwargs = dict(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            # ThinkingConfig.thinking_budget requires google-genai>=1.5; older versions
            # only support thinking_mode. Gracefully degrade if the parameter is rejected.
            try:
                config_kwargs['thinking_config'] = genai_types.ThinkingConfig(
                    thinking_budget=thinking_budget,
                )
            except Exception:
                # Fallback for older SDK: use thinking_mode instead
                try:
                    mode = "DISABLED" if thinking_budget == 0 else "ENABLED"
                    config_kwargs['thinking_config'] = genai_types.ThinkingConfig(
                        thinking_mode=mode,
                    )
                except Exception:
                    pass  # Skip thinking config entirely
            gen_config = genai_types.GenerateContentConfig(**config_kwargs)
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=gen_config,
            )
            # Guard against empty/blocked responses
            if not response.candidates or not response.text:
                logger.warning(f"Gemini returned empty response (blocked or error)")
                return ""
            result = response.text.strip()
            elapsed = time.time() - start
            self._request_count += 1
            self._total_time += elapsed
            # Log token usage and estimated cost for monitoring
            usage = getattr(response, 'usage_metadata', None)
            input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
            output_tokens = getattr(usage, 'candidates_token_count', None) or getattr(usage, 'output_token_count', 0) or 0
            thinking_used = getattr(usage, 'thoughts_token_count', 0) or 0
            # Cost estimate: Gemini 2.5 Flash — $0.15/1M input, $0.60/1M output, $3.50/1M thinking
            est_cost = (input_tokens * 0.15 + output_tokens * 0.60 + thinking_used * 3.50) / 1_000_000
            logger.info(
                f"💰 Gemini [{self.model_name}]: {len(result)} chars in {elapsed:.1f}s | "
                f"in={input_tokens} out={output_tokens} think={thinking_used} | "
                f"~${est_cost:.4f}"
            )
            return result
        except Exception as e:
            self._error_count += 1
            logger.error(f"Gemini generation error: {e}")
            return ""

    async def _agenerate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024, thinking_budget: int = 0) -> str:
        """Async wrapper — Gemini SDK is sync, so we run in executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._generate, prompt, temperature, max_tokens, thinking_budget)

    def _generate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 1024) -> Optional[Dict]:
        """Generate structured JSON from Gemini (thinking disabled for cost savings)."""
        result = self._generate(prompt, temperature=temperature, max_tokens=max_tokens, thinking_budget=0)
        if not result:
            return None
        return _repair_json(result)

    async def _agenerate_json(self, prompt: str, temperature: float = 0.0, max_tokens: int = 1024) -> Optional[Dict]:
        """Async JSON generation (thinking disabled for cost savings)."""
        result = await self._agenerate(prompt, temperature=temperature, max_tokens=max_tokens, thinking_budget=0)
        if not result:
            return None
        return _repair_json(result)

    # ==================================================================
    # RESUME PARSING
    # ==================================================================

    async def parse_resume(self, text: str) -> Optional[Dict]:
        """Parse resume text using Gemini for structured extraction."""
        if not text or len(text.strip()) < 30:
            return None

        cache_key = self._cache_key("resume", text)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        from services.job_taxonomy import classify_job_title

        # Gemini 2.5 Flash supports 1M tokens — send up to 12K chars for thorough extraction
        prompt = f"""You are an expert resume parser. Extract structured data from the resume below. Return ONLY valid JSON — no commentary, no markdown.

════ RESUME ════
{text[:12000]}
════ END ════

EXTRACTION RULES:
• name: Full name exactly as written (First Last). Use the most prominent name at the top.
• email: Exact email address. Leave empty if none found.
• phone: Include country code if present (e.g., "+971 50 123 4567"). Leave empty if none.
• location: "City, Country" or "City" format. Extract from address/header section.
• nationality: ONLY if explicitly stated (e.g., "Indian national", "UAE citizen", "Pakistani"). Leave empty if not stated.
• linkedin: Full URL or just the handle. Leave empty if none.
• summary: Write a 3-4 sentence professional summary based on the resume content.
• skills: Extract ALL technical skills, tools, frameworks, programming languages, platforms, methodologies, and domain expertise. Be exhaustive — missing a skill is worse than including a borderline one.
• experience_years: Sum all work roles from dates. If explicitly stated (e.g., "8 years experience"), use that value. Round to 1 decimal. "Present" means current.
• work_history: ALL job positions, newest first. Duration = calculated time in role ("2 years 3 months"). Description = 1-2 sentences of actual responsibilities.
• education: Real academic degrees only (B.Tech, MBA, Ph.D, Diploma, B.Sc). NOT certifications, NOT training courses.
• certifications: Professional certifications only (AWS Certified, PMP, CFA, CISSP, etc.). NOT degrees.
• languages: As stated (e.g., "English - Fluent", "Arabic - Basic"). Leave empty array if not mentioned.
• notice_period: Exact text as found: "Immediate", "30 days", "1 month", "2 months", "Serving notice", "3 months notice". Leave empty if not stated.
• current_salary: As written in resume — keep original format: "12 LPA", "AED 15,000/month", "50K USD", "25,000 INR". Leave empty if not stated.
• expected_salary: As written. Leave empty if not stated.
• source_portal: Job portal if mentioned anywhere (Indeed, LinkedIn, Naukri, Bayt, GulfTalent). Leave empty if unclear.
• job_applied_for: Position candidate is applying for if stated. Otherwise leave empty — do NOT guess from work history.

Return EXACTLY this JSON structure (no extra fields, no missing fields):
{{
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "nationality": "",
    "linkedin": "",
    "summary": "",
    "skills": [],
    "experience_years": 0,
    "work_history": [{{"title": "", "company": "", "period": "", "duration": "", "description": ""}}],
    "education": [{{"degree": "", "field": "", "institution": "", "year": ""}}],
    "certifications": [],
    "languages": [],
    "notice_period": "",
    "current_salary": "",
    "expected_salary": "",
    "source_portal": "",
    "job_applied_for": ""
}}

CRITICAL: NEVER fabricate data. Use empty string or empty array when data is absent. nationality/salary/notice_period must be empty unless EXPLICITLY written in the resume."""

        result = await self._agenerate_json(prompt, temperature=0.05, max_tokens=2048)

        if result:
            # ── Sanitize fields: strip CID artifacts and garbage from AI output ──
            for field in ('phone', 'name', 'email', 'location', 'linkedin', 'summary'):
                val = result.get(field, '')
                if val and isinstance(val, str) and 'cid:' in val:
                    result[field] = _CID_RE.sub('', val).strip()

            # ── Validate phone: must contain real digits, not garbage ──
            phone = result.get('phone', '')
            if phone:
                digits = _DIGITS_ONLY_RE.sub('', phone)
                if len(digits) < 7 or len(digits) > 15 or 'cid' in phone.lower():
                    logger.warning(f"🚫 Rejected garbage phone: {phone[:50]}")
                    result['phone'] = ''
            
            # ── Normalize field names: job_title_applied → job_applied_for ──
            # Gemini prompt asks for both; unify to what the DB expects
            if result.get('job_title_applied') and not result.get('job_applied_for'):
                result['job_applied_for'] = result.pop('job_title_applied')
            elif result.get('job_title_applied'):
                result.pop('job_title_applied', None)
            
            # ── Normalize experience_years → experience ──
            if 'experience_years' in result and 'experience' not in result:
                result['experience'] = result.get('experience_years', 0)
            
            # Validate category
            if not result.get('job_subcategory') or result.get('job_category') == 'General':
                titles = [w.get('title', '') for w in result.get('work_history', []) if isinstance(w, dict)]
                if titles:
                    cat, sub = classify_job_title(titles[0])
                    result['job_category'] = cat
                    result['job_subcategory'] = sub

            self._set_cache(cache_key, result)
            logger.info(f"📄 [Gemini] Resume parsed: {result.get('name', 'Unknown')} | "
                        f"Skills: {len(result.get('skills', []))} | "
                        f"Exp: {result.get('experience_years', 0)}yrs")

        return result

    # ==================================================================
    # EMAIL CANDIDATE EXTRACTION
    # ==================================================================

    async def parse_candidate_email(self, subject: str, body: str, sender: str = "", resume_text: str = "") -> Optional[Dict]:
        """Parse candidate email using Gemini with comprehensive extraction.
        
        Args:
            subject: Email subject line
            body: Email body text
            sender: Sender email address
            resume_text: Optional raw resume text (from PDF/DOCX attachment) for richer extraction
        """
        if not body or len(body.strip()) < 20:
            return None

        cache_key = self._cache_key("email", f"{subject}:{body[:500]}:{resume_text[:200] if resume_text else ''}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        source = "Direct"
        body_lower = body.lower()
        if "indeed" in body_lower:
            source = "Indeed"
        elif "linkedin" in body_lower:
            source = "LinkedIn"
        elif "naukri" in body_lower:
            source = "Naukri"
        elif "bayt" in body_lower:
            source = "Bayt"
        elif "gulftalent" in body_lower:
            source = "GulfTalent"
        elif "glassdoor" in body_lower:
            source = "Glassdoor"

        from services.job_taxonomy import classify_job_title, get_taxonomy_prompt_text

        # Build combined text — email body + resume if available
        body_section = body[:4000]
        resume_section = ""
        if resume_text and len(resume_text.strip()) > 50:
            resume_section = f"\n\nRESUME ATTACHMENT:\n{resume_text[:5000]}"

        taxonomy_text = get_taxonomy_prompt_text()

        prompt = f"""You are a recruitment email parser. Extract candidate data as JSON. Return ONLY valid JSON — no commentary.

SUBJECT: {subject}
SENDER: {sender}
SOURCE: {source}

EMAIL BODY:
{body_section}
{resume_section}

EXTRACTION RULES:
• is_candidate_email: false if NOT a job application (newsletters, invoices, system notifications, internal emails). true otherwise.
• name: Full name of the applicant. Extract from email signature, body, or resume.
• email: Candidate's PERSONAL email only. NEVER use portal-generated addresses (cv@, resume@, careers@, apply@, recruitment@, noreply@, do-not-reply@). Leave empty if only portal address found.
• phone: With country code if present. Leave empty if none.
• location: "City, Country" from email or resume. Leave empty if not found.
• nationality: Only if explicitly stated ("Indian national", "UAE citizen"). Leave empty if not stated.
• skills: ALL technical skills, tools, frameworks, languages, platforms mentioned in email + resume. Be exhaustive.
• experience_years: From stated value or sum of work dates. 0 if unknown.
• summary: 2-3 sentence professional summary from email/resume content.
• linkedin: URL or handle. Leave empty if none.
• job_applied_for: Role mentioned in subject line or email body. Leave empty if unclear.
• notice_period: As stated: "Immediate", "30 days", "1 month", "Serving notice". Leave empty if not mentioned.
• current_salary: Keep original format: "12 LPA", "AED 15,000/month". Leave empty if not stated.
• expected_salary: Keep original format. Leave empty if not stated.
• work_history: Actual job positions only (NOT education). Newest first.
• education: Real degrees only (B.Tech, MBA, Ph.D, Diploma). NOT certifications or training.
• certifications: Professional certs (AWS, PMP, CFA). NOT degrees.
• job_category + job_subcategory: Pick EXACT names from taxonomy below. Use "General" / "General Professional" if no match.
• quality_score: Score 0-100 the ACTUAL candidate using ALL criteria simultaneously:
  90-100: 10+ yrs exp, 10+ skills, degree+certifications, leadership/rare expertise, complete profile
  75-89: 5-10 yrs exp, 7+ relevant skills, degree or strong certs, specialized knowledge, good profile
  60-74: 3-6 yrs exp, 5+ skills, some education or certs, solid work history, decent profile
  45-59: 1-4 yrs exp, 3-5 skills, basic education, limited history, partial profile
  25-44: <2 yrs exp OR very few skills, student/fresh grad, or very thin profile
  <25: No real work history, no skills, barely any information
  NEVER default to 50. Score 0 means "no data to assess" (prefer 0 over a guess).

TAXONOMY (use EXACT names):
{taxonomy_text}

Return EXACTLY this JSON:
{{
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "nationality": "",
    "linkedin": "",
    "summary": "",
    "skills": [],
    "experience_years": 0,
    "job_applied_for": "",
    "source": "{source}",
    "notice_period": "",
    "current_salary": "",
    "expected_salary": "",
    "work_history": [{{"title": "", "company": "", "period": "", "description": ""}}],
    "education": [{{"degree": "", "field": "", "institution": "", "year": ""}}],
    "certifications": [],
    "languages": [],
    "job_category": "",
    "job_subcategory": "",
    "quality_score": 0,
    "is_candidate_email": true
}}"""

        try:
            result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)
        except Exception as gen_err:
            logger.warning(f"Gemini JSON generation error: {gen_err}")
            return None

        if result and isinstance(result, dict):
            if not result.get('is_candidate_email', True):
                return None
            result['source'] = source
            # Normalize field names to match what the pipeline expects
            if 'experience_years' in result and 'experience' not in result:
                result['experience'] = result.get('experience_years', 0)
            if result.get('job_title_applied') and not result.get('job_applied_for'):
                result['job_applied_for'] = result.pop('job_title_applied')
            elif result.get('job_title_applied'):
                result.pop('job_title_applied', None)
            # Validate category
            if not result.get('job_subcategory'):
                title = result.get('job_applied_for', '')
                if title:
                    cat, sub = classify_job_title(title)
                    result['job_category'] = cat
                    result['job_subcategory'] = sub
            # Clamp + floor quality_score using extractable data signals
            raw_qs = result.get('quality_score', 0)
            try:
                qs = max(0, min(100, int(float(raw_qs))))
            except (TypeError, ValueError):
                qs = 0
            # Data-driven floor: if Gemini returned 0 but we have concrete data, compute a floor
            if qs == 0:
                _s = result.get('skills', [])
                _e = result.get('experience', result.get('experience_years', 0)) or 0
                try:
                    _e = int(float(_e))
                except (TypeError, ValueError):
                    _e = 0
                _has_edu = bool(result.get('education'))
                _has_certs = bool(result.get('certifications'))
                _has_summary = bool(str(result.get('summary', '')).strip())
                _data_floor = 10 + min(25, len(_s) * 3) + min(20, _e * 3) + (8 if _has_edu else 0) + (5 if _has_certs else 0) + (3 if _has_summary else 0)
                if len(_s) > 0 or _e > 0:
                    qs = min(75, max(15, _data_floor))
            result['quality_score'] = qs
            if result.get('name') or result.get('email'):
                self._set_cache(cache_key, result)
                logger.info(f"[Gemini] Email parsed: {result.get('name', 'Unknown')} | Source: {source}")
                return result

        return None

    # ==================================================================
    # CANDIDATE TEXT ANALYSIS (for background processing)
    # ==================================================================

    async def analyze_candidate(self, text: str, job_context: str = None) -> Dict:
        """Analyze raw candidate/resume text and extract structured data.
        Used by the background processor to enrich unprocessed candidates.
        If job_context is provided, scores reflect fit for that role."""
        if not text or len(text.strip()) < 20:
            return {}

        cache_key = self._cache_key("analyze", text[:500] + (job_context or '')[:200])
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        job_instruction = ""
        if job_context:
            job_instruction = f"""
TARGET ROLE CONTEXT:
{job_context[:1000]}

Score the candidate's FIT for this specific role. A 90%+ means excellent match for this exact position."""
        else:
            job_instruction = """
No specific target role provided. Score based on OVERALL professional quality:
- 85-100: Exceptional — 10+ yrs experience, strong skills, leadership, certifications
- 70-84: Strong — 5+ yrs, good skills match, relevant education
- 55-69: Moderate — 2-5 yrs, some relevant skills but gaps
- 40-54: Developing — Entry-level, limited skills, minimal experience
- Below 40: Weak — Unclear background or very junior

Be precise and differentiated. Do NOT default to 25, 50, or 65. Assess the actual resume quality carefully.
A resume with ANY meaningful skills or experience MUST score at least 40.
If the resume shows 10+ skills, clear experience, and education, score 70-85.
If 5-9 skills with 2+ years experience, score 55-70.
If 3-4 skills and 1-2 years, score 45-55.
If sparse/generic with minimal info, score 30-45.
Only score below 30 if the resume is nearly empty or unintelligible."""

        prompt = f"""Recruitment AI: analyze resume, extract structured data, assess quality.
{job_instruction}

RESUME:
{text[:4000]}

Return JSON:
{{
    "name": "Full Name",
    "phone": "Phone with country code",
    "email": "PERSONAL email only (ignore cv@, resume@, careers@, jobs@, apply@)",
    "location": "City, Country",
    "skills": ["ALL technical/professional skills — be thorough"],
    "experience": 5,
    "education": ["Highest degree e.g. B.Tech CS, MBA"],
    "job_category": "One of: Software Engineer, DevOps Engineer, Data Scientist, Cybersecurity, QA / Testing, IT & Systems, Product Manager, Design, Project Management, Business Analyst, Consulting, Marketing, Content & Communications, Sales, Finance, HR, Executive, Legal, Healthcare, Education, Customer Service, Operations, General",
    "job_subcategory": "Specific role title",
    "quality_score": "<integer 10-100>",
    "summary": "2-3 sentence summary",
    "certifications": ["certs"],
    "languages": ["languages"],
    "linkedin": "",
    "work_history": [{"title": "", "company": "", "period": "", "duration": "", "description": ""}]
}}

quality_score rules: 10+ skills + 5+ yrs + degree = 70-85 | 5-9 skills + 2-5 yrs = 55-70 | 3-4 skills + 1-2 yrs = 45-55 | Nearly empty = below 30 | Any skills/experience = min 35. Never default to 25/50/65."""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)

        if result:
            # Normalize score — prefer quality_score, then match_score
            score = result.get('quality_score')
            if score is None or score is False:
                score = result.get('match_score')
            if score is None:
                # Calculate a reasonable fallback from extracted data
                skills_count = len(result.get('skills', []))
                exp = result.get('experience', 0) or 0
                has_edu = bool(result.get('education'))
                has_certs = bool(result.get('certifications'))
                score = 25  # base
                score += min(30, skills_count * 3)  # up to 30 from skills
                score += min(25, exp * 3)  # up to 25 from experience  
                score += 10 if has_edu else 0
                score += 5 if has_certs else 0
                score = min(95, max(15, score))
                logger.info(f"📊 Calculated fallback score: {score} (skills={skills_count}, exp={exp})")
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                if len(nums) >= 2:
                    # Range like "50-75" → take average
                    score = (int(nums[0]) + int(nums[1])) // 2
                elif nums:
                    score = int(nums[0])
                else:
                    score = 40
            try:
                result['match_score'] = max(10, min(100, int(float(score))))
            except (TypeError, ValueError):
                result['match_score'] = 40
            result['quality_score'] = result['match_score']
            result.setdefault('job_category', 'General')
            result.setdefault('skills', [])
            result.setdefault('experience', 0)
            result.setdefault('summary', '')

            # ── Post-AI score validation: enforce minimum floors based on extracted data ──
            # Prevents AI from severely under-scoring candidates with strong profiles,
            # but caps the boost to avoid overriding legitimate AI assessment (max +15 points).
            ai_score = result['match_score']
            skills_count = len(result.get('skills', []))
            exp = result.get('experience', 0) or 0
            has_edu = bool(result.get('education'))
            has_certs = bool(result.get('certifications'))
            data_score = 25
            data_score += min(30, skills_count * 3)
            data_score += min(25, exp * 3)
            data_score += 10 if has_edu else 0
            data_score += 5 if has_certs else 0
            data_score = min(90, max(15, data_score))
            if ai_score < data_score:
                # Cap the boost: never override AI by more than 15 points
                boosted = min(data_score, ai_score + 15)
                result['match_score'] = boosted
                result['quality_score'] = boosted
                logger.info(f"📊 Score boosted: AI={ai_score} → floor={data_score}, capped={boosted} "
                            f"(skills={skills_count}, exp={exp})")

            # ── Reclassify General category using skills/subcategory ──
            if result.get('job_category') == 'General':
                sub = result.get('job_subcategory', '')
                if sub:
                    from services.job_taxonomy import classify_job_title
                    cat, new_sub = classify_job_title(sub)
                    if cat != 'General':
                        result['job_category'] = cat
                        result['job_subcategory'] = new_sub
                if result.get('job_category') == 'General':
                    # Try classifying from skills
                    skills_lower = [s.lower() for s in result.get('skills', [])]
                    tech_skills = {'python', 'java', 'javascript', 'react', 'angular', 'vue',
                                   'node', 'django', 'flask', '.net', 'c#', 'c++', 'go', 'rust',
                                   'typescript', 'php', 'ruby', 'swift', 'kotlin', 'flutter',
                                   'html', 'css', 'sql', 'nosql', 'mongodb', 'postgresql',
                                   'mysql', 'redis', 'docker', 'kubernetes', 'aws', 'azure',
                                   'gcp', 'terraform', 'ci/cd', 'git', 'github', 'gitlab',
                                   'microservices', 'rest', 'api', 'graphql', 'full-stack',
                                   'frontend', 'backend', 'devops', 'cloud', 'bootstrap',
                                   'spring', 'express', 'fastapi', 'nextjs', 'nuxt'}
                    data_skills = {'power bi', 'tableau', 'pandas', 'numpy', 'tensorflow',
                                   'pytorch', 'scikit-learn', 'machine learning', 'ai',
                                   'data science', 'nlp', 'computer vision', 'spark',
                                   'hadoop', 'etl', 'data warehouse', 'bi'}
                    security_skills = {'penetration testing', 'soc', 'siem', 'firewall',
                                       'cybersecurity', 'encryption', 'vulnerability',
                                       'nmap', 'wireshark', 'burp suite'}
                    marketing_skills = {'seo', 'sem', 'google ads', 'facebook ads',
                                        'content marketing', 'hubspot', 'mailchimp',
                                        'social media', 'branding', 'copywriting'}
                    sales_skills = {'salesforce', 'crm', 'lead generation', 'cold calling',
                                    'b2b', 'b2c', 'account management', 'pipeline'}
                    finance_skills = {'accounting', 'audit', 'tax', 'financial analysis',
                                      'budgeting', 'forecasting', 'gaap', 'ifrs', 'sap',
                                      'quickbooks', 'erp'}
                    skills_set = set(skills_lower)
                    if len(skills_set & tech_skills) >= 3:
                        result['job_category'] = 'Software Engineering'
                        result['job_subcategory'] = result.get('job_subcategory') or 'Software Engineering'
                    elif len(skills_set & data_skills) >= 2:
                        result['job_category'] = 'Data & Analytics'
                        result['job_subcategory'] = result.get('job_subcategory') or 'Data & Analytics'
                    elif len(skills_set & security_skills) >= 2:
                        result['job_category'] = 'Cybersecurity'
                        result['job_subcategory'] = result.get('job_subcategory') or 'Cybersecurity'
                    elif len(skills_set & marketing_skills) >= 2:
                        result['job_category'] = 'Marketing'
                        result['job_subcategory'] = result.get('job_subcategory') or 'Marketing'
                    elif len(skills_set & sales_skills) >= 2:
                        result['job_category'] = 'Sales'
                        result['job_subcategory'] = result.get('job_subcategory') or 'Sales'
                    elif len(skills_set & finance_skills) >= 2:
                        result['job_category'] = 'Finance & Accounting'
                        result['job_subcategory'] = result.get('job_subcategory') or 'Finance & Accounting'

            self._set_cache(cache_key, result)
            logger.info(f"📊 [Gemini] Analyzed candidate: score={result['match_score']}, category={result['job_category']}")
            return result

        return {}

    # ==================================================================
    # DEEP CANDIDATE ANALYSIS
    # ==================================================================

    async def analyze_candidate_deep(self, candidate_data: Dict) -> Dict:
        """Deep AI analysis with pros/cons and hiring recommendation."""
        cache_key = self._cache_key("deep", json.dumps(candidate_data, default=str))
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        education = candidate_data.get('education', [])
        work_history = candidate_data.get('work_history', candidate_data.get('workHistory', []))
        summary = candidate_data.get('summary', '')
        resume_text = candidate_data.get('resume_text', '')

        work_text = ""
        if work_history:
            for w in work_history[:6]:
                if isinstance(w, dict):
                    title = w.get('title', w.get('position', ''))
                    company = w.get('company', w.get('organization', ''))
                    period = w.get('period', w.get('duration', w.get('years', '')))
                    desc = w.get('description', w.get('responsibilities', ''))
                    work_text += f"\n  - {title} at {company} ({period})"
                    if desc:
                        work_text += f" — {str(desc)[:150]}"

        edu_text = ""
        if education:
            for e in education[:4]:
                if isinstance(e, dict):
                    degree = e.get('degree', e.get('title', ''))
                    field = e.get('field', '')
                    inst = e.get('institution', e.get('school', ''))
                    year = e.get('year', e.get('graduation_year', ''))
                    edu_text += f"\n  - {degree}{' in ' + field if field else ''} — {inst} ({year})"
                elif isinstance(e, str):
                    edu_text += f"\n  - {e}"

        # Include resume text for richer analysis if available
        resume_section = ""
        if resume_text:
            resume_section = f"\n\nRESUME TEXT (raw — use for deeper analysis):\n{resume_text[:3000]}"

        prompt = f"""You are a world-class senior recruiter with 20+ years experience. Analyze this candidate thoroughly and provide a detailed, data-driven assessment. Return ONLY valid JSON.

CANDIDATE: {name}
Experience: {experience} years
Skills: {', '.join(skills[:25]) if skills else 'Not listed'}
Education:{edu_text or ' N/A'}
Work History:{work_text or ' N/A'}
Summary: {summary[:500] if summary else 'N/A'}
{resume_section}

IMPORTANT EXTRACTION RULES:
- For education: Extract REAL academic degrees only (B.Tech, MBA, M.Sc, etc.). Do NOT extract skills, certifications, or resume section headers as degrees.
- If education data looks garbled or nonsensical (e.g. "Masters in Processes"), ignore it and try to extract from the resume text instead.
- For work history: Extract actual job positions with title, company, and duration. Do NOT mix education sections with work history.
- For the candidate's email: If resume text contains a personal email address (gmail, yahoo, hotmail, outlook, etc.), extract it. Do NOT use portal emails like cv@, jobs@, careers@, recruitment@, noreply@, apply@ addresses.

Return JSON with ALL these fields:
{{
    "executive_summary": "3-4 sentence thorough assessment covering experience level, key strengths, specialization, and overall hire-worthiness",
    "technical_assessment": "2-3 sentences on technical capabilities, tools mastery, and technical depth",
    "experience_assessment": "2-3 sentences analyzing career progression, tenure patterns, and industry experience",
    "education_assessment": "1-2 sentences on educational background and relevance",
    "career_trajectory": "2-3 sentences on career growth pattern and future potential",
    "pros": ["Specific strength 1 with evidence", "Specific strength 2", "Specific strength 3", "Specific strength 4"],
    "cons": ["Specific concern 1 with reasoning", "Specific concern 2"],
    "ideal_roles": ["Best-fit role 1", "Best-fit role 2", "Best-fit role 3"],
    "interview_focus_areas": ["Area 1 — why important", "Area 2 — why important", "Area 3"],
    "hiring_recommendation": "STRONGLY_RECOMMEND or RECOMMEND or CONSIDER or PASS",
    "hiring_recommendation_rationale": "2-3 sentences explaining the recommendation",
    "confidence_score": 80,
    "overall_rating": "A+ or A or A- or B+ or B or B- or C+ or C or D",
    "candidate_email": "personal email from resume if found, otherwise empty string"
}}

SCORING GUIDELINES:
- A+/A (STRONGLY_RECOMMEND): 8+ years, deep expertise, leadership, certifications, strong progression
- A-/B+ (RECOMMEND): 5-8 years, solid skills, good education, clear growth
- B/B- (CONSIDER): 2-5 years, relevant skills but gaps, developing career
- C+/C (CONSIDER/PASS): Entry-level, limited skills, unclear trajectory
- D (PASS): Misaligned background, significant gaps

Be specific — reference actual skills, companies, and experience from the profile. Never be generic."""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)

        if result:
            result.setdefault('overall_assessment', result.get('executive_summary', ''))
            result.setdefault('strengths', result.get('pros', [])[:5])
            result.setdefault('weaknesses', result.get('cons', [])[:3])
            result.setdefault('recommended_roles', result.get('ideal_roles', []))
            self._set_cache(cache_key, result)
            logger.info(f"🔍 [Gemini] Deep Analysis: {name} → {result.get('hiring_recommendation', 'N/A')}")

        return result or {
            'executive_summary': f'{name} has {experience} years of experience.',
            'pros': ['Application submitted'],
            'cons': ['AI analysis unavailable'],
            'hiring_recommendation': 'CONSIDER',
            'confidence_score': 30,
            'overall_rating': 'C',
            'overall_assessment': f'{name} profile requires manual review.',
            'strengths': ['Resume submitted'],
            'weaknesses': ['Analysis unavailable'],
            'recommended_roles': ['General'],
        }

    # ==================================================================
    # CANDIDATE-JOB MATCHING
    # ==================================================================

    async def match_candidate_to_job(self, candidate_data: Dict, job_description: str) -> Dict:
        """Match a single candidate against a job description."""
        import hashlib as _hl
        _jd_hash = _hl.sha256((job_description or '').encode()).hexdigest()[:16]
        cache_key = self._cache_key("match", f"{json.dumps(candidate_data, default=str)}:{_jd_hash}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))

        # Build the same rich candidate block as _batch_match uses
        skills_str = ', '.join((candidate_data.get('skills') or [])[:20])
        exp = candidate_data.get('experience', 0)
        location = candidate_data.get('location', '')
        notice = candidate_data.get('notice_period', '')
        salary = candidate_data.get('current_salary', '') or candidate_data.get('expected_salary', '')
        wh = candidate_data.get('workHistory') or candidate_data.get('work_history') or []
        wh_lines = []
        for w in wh[:3]:
            if isinstance(w, dict):
                wh_lines.append(f"  - {w.get('title','')} at {w.get('company','')} ({w.get('duration','')}) : {w.get('description','')[:120]}")
        work_text = '\n'.join(wh_lines) if wh_lines else '  Not provided'
        edu = candidate_data.get('education') or []
        edu_text = '; '.join([e.get('degree','') + ' ' + e.get('field','') if isinstance(e, dict) else str(e) for e in edu[:2]]) or 'Not provided'
        certs = ', '.join(candidate_data.get('certifications') or []) or 'None'
        summary = candidate_data.get('summary', '')

        prompt = f"""Evaluate candidate-job fit. Return ONLY valid JSON.

CANDIDATE: {name}
Skills: {skills_str}
Experience: {exp} years | Location: {location}
Notice Period: {notice} | Salary: {salary}
Work History:
{work_text}
Education: {edu_text}
Certifications: {certs}
Summary: {summary[:400]}

JOB:
{job_description[:3000]}

Return JSON:
{{
    "match_score": 75,
    "skill_match_score": 80,
    "experience_match_score": 70,
    "overall_fit": "Good Match",
    "matched_skills": ["skill1"],
    "missing_skills": ["skill1"],
    "strengths": ["str1", "str2"],
    "gaps": ["gap1"],
    "recommendation": "2-3 sentence fit summary",
    "interview_questions": ["q1", "q2"],
    "risk_factors": ["risk1"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)

        if result:
            score = result.get('match_score', 50)
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 50
            result['match_score'] = max(0, min(100, int(score)))
            self._set_cache(cache_key, result)
            logger.info(f"🎯 [Gemini] Match: {name} → {result['match_score']}%")

        return result or {
            'match_score': 0,
            'matched_skills': [],
            'missing_skills': [],
            'strengths': [],
            'gaps': ['Analysis unavailable'],
            'recommendation': 'Manual review required',
        }

    # ==================================================================
    # BATCH CANDIDATE RANKING
    # ==================================================================

    async def rank_candidates_for_job(
        self,
        candidates: List[Dict],
        job_description: str,
        top_n: int = 10
    ) -> List[Dict]:
        """
        Rank candidates against a JD using pre-filter + batch Gemini scoring.
        Same 2-stage approach as LLMService for consistency.
        """
        start = time.time()

        # Stage 1: Fast keyword pre-filter using shared constants
        jd_lower = job_description.lower()
        jd_tokens = set(re.sub(r'[^\w\s#+.]', ' ', jd_lower).split())
        jd_keywords = {t for t in jd_tokens if len(t) >= 2 and t not in STOP_WORDS}

        expanded_keywords = set(jd_keywords)
        for alias, expansions in LOCATION_ALIASES.items():
            if alias in jd_keywords:
                expanded_keywords.update(expansions)

        # Expand with skill synonyms: if JD mentions "react", also match "reactjs", "react.js"
        synonym_additions = set()
        for kw in jd_keywords:
            syns = SKILL_SYNONYMS.get(kw, set())
            if syns:
                synonym_additions.update(syns)
            # Also reverse-lookup: if kw is a synonym value, add the canonical key
            for canonical, syn_set in SKILL_SYNONYMS.items():
                if kw in syn_set:
                    synonym_additions.add(canonical)
                    # Do NOT add all syn_set members — they contaminate unrelated queries
        expanded_keywords.update(synonym_additions)

        # Detect explicit location requirement from JD
        raw_loc_terms = _extract_location_from_text(job_description)
        jd_location_terms = _expand_location_terms(raw_loc_terms)
        jd_has_location = len(jd_location_terms) > 0

        # Detect experience requirement (range + min)
        jd_min_experience = 0
        jd_max_experience = 999
        _range_match_jd = EXPERIENCE_RANGE_PATTERN.search(job_description)
        if _range_match_jd:
            groups = _range_match_jd.groups()
            if groups[0] and groups[1]:
                jd_min_experience = int(groups[0]); jd_max_experience = int(groups[1])
            elif groups[2] and groups[3]:
                jd_min_experience = int(groups[2]); jd_max_experience = int(groups[3])
            elif groups[4] and groups[5]:
                jd_min_experience = int(groups[4]); jd_max_experience = int(groups[5])
            elif groups[6]:
                jd_max_experience = int(groups[6])
            elif groups[7]:
                jd_max_experience = int(groups[7])
            elif groups[8]:
                jd_max_experience = int(groups[8])
            elif groups[9]:
                jd_max_experience = int(groups[9])
            elif groups[10] and groups[11]:
                jd_min_experience = int(groups[10]); jd_max_experience = int(groups[11])
            if jd_min_experience > jd_max_experience:
                jd_min_experience, jd_max_experience = jd_max_experience, jd_min_experience
        else:
            _exp_match_jd = EXPERIENCE_PATTERN.search(job_description)
            jd_min_experience = int(_exp_match_jd.group(1)) if _exp_match_jd else 0

        # Detect seniority level
        _seniority_match_jd = SENIORITY_PATTERN.search(job_description)
        jd_seniority = _seniority_match_jd.group(1).lower().strip() if _seniority_match_jd else None

        # Detect negative/exclusion terms
        jd_negative_terms: set = set()
        _neg_match_jd = NEGATIVE_PATTERN.search(job_description)
        if _neg_match_jd:
            neg_raw = _neg_match_jd.group(1).strip().lower()
            for part in re.split(r'[,;&]+|\s+or\s+', neg_raw):
                part = part.strip()
                part = re.sub(r'\s+(background|experience|skills?|developers?|engineers?|knowledge|expertise)$', '', part)
                if part and part not in STOP_WORDS and len(part) > 1:
                    jd_negative_terms.add(part)

        pre_scored = []
        for idx, c in enumerate(candidates):
            skills = [s.lower().strip() for s in c.get('skills', [])]
            category = (c.get('jobCategory') or c.get('job_category') or '').lower()
            subcategory = (c.get('jobSubcategory') or c.get('job_subcategory') or '').lower()
            location = (c.get('location') or '').lower()
            summary = (c.get('summary') or '').lower()
            name = (c.get('name') or '').lower()

            exp = _safe_int_experience(c.get('experience', 0))

            # ── HARD FILTER: Experience range ──
            if jd_max_experience < 999 and exp > jd_max_experience:
                continue  # Exceeds maximum — skip
            if jd_min_experience > 0 and exp < jd_min_experience:
                continue  # Below minimum — skip

            # ── HARD FILTER: Negative terms ──
            if jd_negative_terms:
                wh_neg = c.get('work_history', [])
                wh_companies = ' '.join(
                    j.get('company', '') for j in (wh_neg if isinstance(wh_neg, list) else []) if isinstance(j, dict)
                ).lower()
                cand_neg_text = f"{name} {' '.join(skills)} {category} {subcategory} {wh_companies}"
                neg_hit = any(re.search(r'\b' + re.escape(neg) + r'\b', cand_neg_text) for neg in jd_negative_terms)
                if neg_hit:
                    continue  # Excluded by negative filter

            # Word-boundary matching for skills with synonym support
            skill_hits = 0
            for s in skills:
                s_words = set(re.sub(r'[^\w\s]', ' ', s).split())
                if s_words & expanded_keywords:
                    skill_hits += 1
                    continue
                matched = False
                for kw in expanded_keywords:
                    if jd_has_location and kw in jd_location_terms:
                        continue
                    if kw == s or (len(kw) >= 3 and (kw in s.split() or s in kw.split())):
                        skill_hits += 1
                        matched = True
                        break
                if not matched:
                    for kw in expanded_keywords:
                        if jd_has_location and kw in jd_location_terms:
                            continue
                        syns = SKILL_SYNONYMS.get(kw, set())
                        if syns and (syns & s_words or s in syns):
                            skill_hits += 1
                            break

            cat_hits = sum(1 for kw in expanded_keywords if kw in category.split() or kw in subcategory.split())

            # Location scoring — strong signal when requirement is explicit
            loc_score_add = 0
            if jd_has_location:
                loc_words = set(re.sub(r'[^\w\s]', ' ', location).split())
                loc_matched = any(lt in location or lt in loc_words for lt in jd_location_terms)
                loc_score_add = 50 if loc_matched else -30
            else:
                loc_hits = sum(1 for kw in expanded_keywords if kw in location)
                loc_score_add = loc_hits * 8

            summary_words = set(summary.split())
            summary_hits = len(summary_words & expanded_keywords)

            # ── Work history: title + company matching ──
            wh = c.get('work_history', [])
            wh_score = 0
            if isinstance(wh, list) and wh:
                for job in wh[:3]:
                    if not isinstance(job, dict):
                        continue
                    jt = (job.get('title', '') or '').lower()
                    jco = (job.get('company', '') or '').lower()
                    jt_words = set(re.sub(r'[^\w\s]', ' ', jt).split())
                    jco_words = set(re.sub(r'[^\w\s]', ' ', jco).split())
                    role_kw_hits = len(jt_words & expanded_keywords)
                    co_kw_hits = len(jco_words & expanded_keywords)
                    wh_score += role_kw_hits * 12 + co_kw_hits * 10

            # ── Certification matching ──
            certs = c.get('certifications', [])
            cert_text = ' '.join(
                (ci if isinstance(ci, str) else (ci.get('name', '') if isinstance(ci, dict) else ''))
                for ci in (certs if isinstance(certs, list) else [])
            ).lower()
            cert_words = set(re.sub(r'[^\w\s]', ' ', cert_text).split())
            cert_hits = len(cert_words & expanded_keywords)

            # ── Language matching ──
            langs = c.get('languages', [])
            lang_text = ' '.join(str(l).lower() for l in (langs if isinstance(langs, list) else []))
            lang_hits = sum(1 for kw in expanded_keywords if kw in lang_text)

            # ── Seniority scoring ──
            seniority_score = 0
            if jd_seniority:
                if jd_seniority in ('junior', 'entry-level', 'entry level', 'fresher', 'fresh graduate', 'intern', 'trainee'):
                    seniority_score = 20 if exp <= 2 else (8 if exp <= 3 else -15)
                elif jd_seniority in ('mid-level', 'mid level', 'midlevel'):
                    seniority_score = 20 if 3 <= exp <= 7 else (8 if 2 <= exp <= 9 else -12)
                elif jd_seniority in ('senior', 'lead', 'principal', 'staff'):
                    seniority_score = 20 if exp >= 7 else (12 if exp >= 5 else -15)
                elif jd_seniority in ('director', 'head', 'vp', 'c-level', 'chief', 'executive'):
                    seniority_score = 20 if exp >= 12 else (8 if exp >= 8 else -20)
                elif jd_seniority == 'manager':
                    seniority_score = 15 if exp >= 5 else (8 if exp >= 3 else -12)

            # ── Experience scoring ──
            exp_score = min(exp, 20) * 1.5  # Up to 30 pts
            if jd_min_experience > 0:
                exp_score += 15 if exp >= jd_min_experience else 0

            pre_score = (
                skill_hits * 15
                + cat_hits * 10
                + loc_score_add
                + min(summary_hits, 5) * 3
                + min(wh_score, 40)
                + cert_hits * 8
                + lang_hits * 8
                + seniority_score
                + exp_score
            )
            pre_scored.append((pre_score, idx, c))

        pre_scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        # Take more candidates for better Gemini recall (5× top_n for higher accuracy)
        deep_count = min(max(top_n * 5, 50), len(pre_scored))
        # Prefer candidates with actual relevance
        relevant = [(s, i, c) for s, i, c in pre_scored if s > 0]
        shortlisted = [c for _, _, c in (relevant[:deep_count] if relevant else pre_scored[:deep_count])]

        logger.info(f"🎯 [Gemini] Pre-filtered {len(candidates)} → {len(shortlisted)} candidates")

        # Stage 2: Batch Gemini scoring (5 per prompt)
        BATCH_SIZE = 5
        results = []

        # Check cache first
        uncached = []
        for c in shortlisted:
            ck = self._cache_key("fast_match", f"{c.get('name','')}:{c.get('email','')}:{hashlib.sha256(job_description.encode()).hexdigest()[:16]}")
            cached = self._get_cached(ck)
            if cached:
                results.append({'candidate': c, 'match': cached, 'score': cached.get('match_score', 0)})
            else:
                uncached.append(c)

        # Batch process uncached — PARALLEL with asyncio.gather for speed
        batches = [uncached[i:i + BATCH_SIZE] for i in range(0, len(uncached), BATCH_SIZE)]

        async def _process_batch(batch):
            batch_results_list = []
            try:
                batch_results = await self._batch_match(batch, job_description)
                for j, c in enumerate(batch):
                    if j < len(batch_results):
                        match_data = batch_results[j]
                        ck = self._cache_key("fast_match", f"{c.get('name','')}:{c.get('email','')}:{hashlib.sha256(job_description.encode()).hexdigest()[:16]}")
                        self._set_cache(ck, match_data)
                        batch_results_list.append({'candidate': c, 'match': match_data, 'score': match_data.get('match_score', 0)})
            except Exception as e:
                logger.warning(f"[Gemini] Batch match error: {e}")
                for c in batch:
                    match = await self.match_candidate_to_job(c, job_description)
                    batch_results_list.append({'candidate': c, 'match': match, 'score': match.get('match_score', 0)})
            return batch_results_list

        # Run up to 4 batches concurrently (rate-limit friendly)
        CONCURRENT_LIMIT = 4
        for chunk_start in range(0, len(batches), CONCURRENT_LIMIT):
            chunk = batches[chunk_start:chunk_start + CONCURRENT_LIMIT]
            batch_outputs = await asyncio.gather(*[_process_batch(b) for b in chunk])
            for batch_output in batch_outputs:
                results.extend(batch_output)

        results.sort(key=lambda x: x['score'], reverse=True)
        elapsed = time.time() - start
        logger.info(f"⚡ [Gemini] Ranking done: {len(results)} results in {elapsed:.1f}s")
        return results[:top_n]

    async def _batch_match(self, batch: List[Dict], job_description: str) -> List[Dict]:
        """Score multiple candidates in a single Gemini call."""
        candidates_text = ""
        for i, c in enumerate(batch, 1):
            skills_str = ', '.join(c.get('skills', [])[:20]) or 'Not specified'
            exp = c.get('experience', 0)
            loc = c.get('location', 'N/A')
            # Build enriched optional fields for better scoring accuracy
            work_hist = c.get('work_history', [])
            work_str = ''
            if isinstance(work_hist, list) and work_hist:
                entries = [
                    f"{j.get('company', '')} ({j.get('title', '')})"
                    + (f" {j.get('period', '')}" if j.get('period') else '')
                    for j in work_hist[:3] if isinstance(j, dict) and j.get('company')
                ]
                if entries:
                    work_str = f"\n  Work History: {', '.join(entries)}"
            certs = c.get('certifications', [])
            cert_str = ''
            if isinstance(certs, list) and certs:
                cert_names = [ci if isinstance(ci, str) else (ci.get('name', '') if isinstance(ci, dict) else '') for ci in certs[:3]]
                cert_names = [n for n in cert_names if n]
                if cert_names:
                    cert_str = f"\n  Certifications: {', '.join(cert_names)}"
            langs = c.get('languages', [])
            lang_str = f"\n  Languages: {', '.join(langs[:4])}" if isinstance(langs, list) and langs else ''
            notice = c.get('notice_period', '')
            notice_str = f"\n  Notice Period: {notice}" if notice else ''
            salary_str = c.get('current_salary', '') or c.get('expected_salary', '') or ''
            salary_disp = f"\n  Salary: {salary_str}" if salary_str else ''
            nationality = c.get('nationality', '')
            nat_str = f"\n  Nationality: {nationality}" if nationality else ''
            # Education
            edu = c.get('education', [])
            edu_str = ''
            if isinstance(edu, list) and edu:
                edu_items = [
                    f"{e.get('degree', '')} {e.get('field', '')} ({e.get('institution', '')})" if isinstance(e, dict) else str(e)
                    for e in edu[:2]
                ]
                edu_items = [e.strip() for e in edu_items if e.strip() and e.strip() != '()']
                if edu_items:
                    edu_str = f"\n  Education: {', '.join(edu_items)}"
            candidates_text += (
                f"\nCANDIDATE {i}: {c.get('name', 'Unknown')}\n"
                f"  Skills: {skills_str}\n"
                f"  Experience: {exp} years\n"
                f"  Location: {loc}\n"
                f"  Summary: {c.get('summary', '')[:200]}"
                f"{work_str}{cert_str}{lang_str}{edu_str}{notice_str}{salary_disp}{nat_str}\n"
            )

        # Build constraints block so Gemini enforces ALL conditions simultaneously
        constraints_lines = []
        _loc_terms = _extract_location_from_text(job_description)
        if _loc_terms:
            constraints_lines.append(f"LOCATION (MANDATORY): {', '.join(_loc_terms)} — non-local candidates = score 0-25")
        _range_m = EXPERIENCE_RANGE_PATTERN.search(job_description)
        if _range_m:
            _g = _range_m.groups()
            if _g[0] and _g[1]:
                constraints_lines.append(f"EXPERIENCE (STRICT): {_g[0]}-{_g[1]} years required — outside range = major deduction")
            elif _g[6]:
                constraints_lines.append(f"EXPERIENCE (STRICT): max {_g[6]} years — over-experienced = major deduction")
        else:
            _em = EXPERIENCE_PATTERN.search(job_description)
            if _em:
                constraints_lines.append(f"EXPERIENCE (MINIMUM): {_em.group(1)}+ years required — below = major deduction")
        _sen_m = SENIORITY_PATTERN.search(job_description)
        if _sen_m:
            constraints_lines.append(f"SENIORITY: {_sen_m.group(1)} level — verify via work history titles, not just years")
        _neg_m = NEGATIVE_PATTERN.search(job_description)
        if _neg_m:
            _neg_raw = _neg_m.group(1).strip().lower()
            jd_negative_terms = set()
            for _part in re.split(r'[,;&]+|\s+or\s+', _neg_raw):
                _part = _part.strip()
                _part = re.sub(r'\s+(background|experience|skills?|developers?|engineers?|knowledge|expertise)$', '', _part)
                if _part and _part not in STOP_WORDS and len(_part) > 1:
                    jd_negative_terms.add(_part)
            if jd_negative_terms:
                neg_str = ', '.join(sorted(jd_negative_terms))
                constraints_lines.append(f"EXCLUDE (HARD): candidates with {neg_str} background = score 0-20")
        # Extract salary range from JD text
        import re as _re
        _salary_match = _re.search(r'(?:salary|compensation|pay|package)[^\n]*?([\d,]+)\s*(?:to|-)\s*([\d,]+)', job_description, _re.IGNORECASE)
        if _salary_match:
            sal_lo, sal_hi = _salary_match.group(1).replace(',', ''), _salary_match.group(2).replace(',', '')
            constraints_lines.append(f"SALARY RANGE: {sal_lo}-{sal_hi} — candidates above this range = 0-30 score")
        constraints_block = "\n".join(f"  • {l}" for l in constraints_lines) if constraints_lines else "  • None — score by overall fit"

        n = len(batch)
        prompt = f"""Score each candidate against the job description. Return ONLY valid JSON with a "candidates" array of EXACTLY {n} objects — one per candidate, in the SAME order.

HARD CONSTRAINTS (R6 — ALL conditions must be met SIMULTANEOUSLY):
{constraints_block}

SCORING RULES:
R1. Work History job titles are the #1 signal — check actual roles held
R2. ALL hard constraints above must be satisfied — a single violation = 0-35 score
R3. Skills listed but never demonstrated in work history = weaker signal
R4. Certifications and languages are strong differentiators when mentioned in JD
R5. For composite queries ("senior Python dev with 5 yrs in Dubai"): EVERY condition is required
R6. ALL hard constraints in the block above must be met SIMULTANEOUSLY
R7. Job hopper penalty: if work history shows more than 4 jobs in 3 years with each tenure under 6 months, reduce score by 15-20 points.
R8. Skills without evidence: if a skill is listed but never appears in work history roles/descriptions, treat it as a weaker signal (50% weight).

{candidates_text}

JOB DESCRIPTION:
{job_description[:3000]}

Each object must have: match_score (0-100 integer — be precise, no defaults), matched_skills (array), missing_skills (array), strengths (array), gaps (array), recommendation (string).

Score guidelines: 85-100 Excellent fit (all constraints met + strong match), 70-84 Strong fit, 55-69 Moderate, 40-54 Weak, <40 Poor or constraint violation. Assess each candidate INDIVIDUALLY.

Return: {{"candidates": [...]}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)
        if not result:
            raise ValueError("Empty Gemini batch response")

        batch_results = result.get('candidates', [])
        if not isinstance(batch_results, list):
            batch_results = []

        normalized = []
        for item in batch_results:
            if not isinstance(item, dict):
                continue
            score = item.get('match_score', 30)
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 30
            item['match_score'] = max(0, min(100, int(score)))
            normalized.append(item)

        # If Gemini returned fewer items than batch size, score remaining individually
        if len(normalized) < n:
            logger.warning(f"[Gemini] Batch returned {len(normalized)}/{n} — scoring remaining individually")
            for idx in range(len(normalized), n):
                try:
                    match = await self.match_candidate_to_job(batch[idx], job_description)
                    normalized.append(match)
                except Exception as e:
                    logger.warning(f"[Gemini] Individual match failed for batch idx {idx}: {e}")
                    normalized.append({
                        'match_score': 30, 'matched_skills': [],
                        'missing_skills': [], 'strengths': ['Needs review'],
                        'gaps': ['Batch scoring incomplete'], 'recommendation': 'Manual review needed'
                    })

        return normalized

    # ==================================================================
    # CANDIDATE COMPARISON
    # ==================================================================

    async def compare_candidates(self, candidates: List[Dict], job_description: Optional[str] = None) -> Dict:
        """Compare multiple candidates side by side."""
        if not candidates or len(candidates) < 2:
            return {'error': 'Need at least 2 candidates'}

        candidates_text = ""
        for i, c in enumerate(candidates[:5], 1):
            candidates_text += f"\nCandidate {i}: {c.get('name', 'Unknown')}\n- Skills: {', '.join(c.get('skills', [])[:10])}\n- Experience: {c.get('experience', 0)} years\n- Score: {c.get('quality_score', c.get('score', 'N/A'))}%\n"

        job_ctx = f"\nJOB:\n{job_description[:1500]}" if job_description else ""

        prompt = f"""Compare these candidates. Return ONLY valid JSON.
{candidates_text}{job_ctx}

Return JSON:
{{
    "ranking": [{{"rank": 1, "name": "Name", "score": 85, "key_advantage": "Why #1", "key_risk": "Main concern"}}],
    "comparison_summary": "Overview",
    "recommendation": "Who to interview first and why",
    "best_for_role": "Best candidate and why"
}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)
        return result or {'ranking': [], 'comparison_summary': 'Comparison unavailable'}

    # ==================================================================
    # AI CHAT
    # ==================================================================

    # ── Query type classification ──
    @staticmethod
    def _classify_query(message: str) -> str:
        """Classify user query to route to the best prompt strategy.
        Returns: 'search', 'analytics', 'advice', 'comparison', 'greeting', 'followup'
        """
        msg = message.lower().strip()

        # Greetings / pleasantries (exact short messages only)
        if msg in ('hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening',
                    'thanks', 'thank you', 'ok', 'okay', 'bye', 'goodbye', 'see you',
                    'welcome', 'sup', 'howdy', 'hola', 'namaste', 'salaam', 'salam'):
            return 'greeting'

        # Follow-up / conversational (short messages referencing prior context)
        if len(msg.split()) <= 4 and any(w in msg for w in ['yes', 'no', 'more', 'next', 'sure', 'go ahead', 'continue', 'elaborate', 'explain', 'details', 'again']):
            return 'followup'

        # Analytics / statistics queries — use word-boundary matching to avoid
        # false positives (e.g. "count" matching inside "accountant")
        analytics_phrases = [
            'how many', 'summary of', 'analyze the database', 'analyze our',
            'number of', 'pie chart', 'bar chart',
        ]
        analytics_words = {
            'count', 'total', 'statistics', 'stats', 'average', 'breakdown',
            'distribution', 'percentage', 'ratio', 'trend', 'report',
            'overview', 'dashboard', 'graph', 'metric', 'kpi',
        }
        # Build word set early (reused later for search signals)
        msg_word_set = set(re.sub(r'[^\w\s]', ' ', msg).split())
        has_analytics = bool(msg_word_set & analytics_words) or any(p in msg for p in analytics_phrases)
        if has_analytics:
            return 'analytics'

        # Comparison queries — word-boundary for short words like "vs"
        comparison_phrases = ['better between', 'side by side', 'which one',
                              'who is better', 'rank these', 'difference between']
        comparison_words = {'compare', 'comparison', 'versus'}
        if bool(msg_word_set & comparison_words) or \
           any(p in msg for p in comparison_phrases) or \
           bool(re.search(r'\bvs\b', msg)):
            return 'comparison'

        # ── Search signal detection (expanded) ──
        # Multi-word phrases: safe to use substring matching
        search_phrases = [
            'based in', 'look for', 'with skills', 'proficient in',
            'who knows', 'who has', 'who can', 'working in', 'worked in',
            'certified in', 'immediate joiner', 'notice period',
            'it company', 'it service', 'banking sector',
        ]
        # Single words: use word-boundary set to avoid false substring matches
        search_words = {
            'find', 'show', 'list', 'candidates', 'shortlist', 'search',
            'filter', 'locate', 'identify', 'recruit', 'who', 'fetch',
            'cvs', 'profiles', 'resumes', 'applicants', 'people',
            'experience', 'years', 'location', 'skills', 'developer',
            'engineer', 'manager', 'designer', 'analyst', 'consultant', 'accountant',
            'administrator', 'coordinator', 'specialist', 'architect', 'director',
            'nurse', 'doctor', 'teacher', 'driver', 'technician', 'executive',
            'available', 'having', 'holding', 'speaks', 'speaking',
            'nationality', 'passport', 'visa',
            'sales', 'marketing', 'finance', 'accounting', 'hr',
            'recruiter', 'programmer', 'tester', 'qa', 'devops', 'data',
            'product', 'project', 'operations', 'logistics', 'procurement',
            'auditor', 'receptionist', 'secretary', 'clerk', 'pharmacist',
            'chef', 'electrician', 'plumber', 'mechanic', 'welder',
            'intern', 'trainee', 'fresher', 'graduate',
            'healthcare', 'manufacturing', 'retail', 'ecommerce', 'startup',
        }
        has_search_signal = bool(msg_word_set & search_words) or any(p in msg for p in search_phrases)

        # Check if any known ROLE TITLE is mentioned — strong search signal
        has_role_signal = bool(msg_word_set & ROLE_TITLES) or any(r in msg for r in ROLE_TITLES if ' ' in r)
        
        # Check if any known SKILL is mentioned — strong search signal even without verbs
        skill_keywords_in_msg = msg_word_set & set(SKILL_SYNONYMS.keys())
        has_skill_signal = len(skill_keywords_in_msg) >= 1
        
        # Also check if any known location is mentioned — strong search signal
        has_location_signal = any(loc in msg for loc in KNOWN_LOCATIONS if ' ' in loc) or \
                             bool(msg_word_set & {loc for loc in KNOWN_LOCATIONS if ' ' not in loc})
        
        # Also check for seniority level mentions — search signal
        has_seniority = bool(SENIORITY_PATTERN.search(msg))
        
        # Also check for experience mentions — search signal
        has_experience = bool(EXPERIENCE_PATTERN.search(msg)) or bool(EXPERIENCE_RANGE_PATTERN.search(msg)) or bool(FRESHER_PATTERN.search(msg))

        # Recruitment advice / general knowledge
        # BUT: if the message also contains candidate search signals, prefer 'search'
        advice_signals = [
            'how to', 'how do i', 'how should', 'what is the best way', 'what are the best',
            'tips for', 'advice on', 'best practices', 'strategy for', 'suggest a',
            'recommend a', 'help me write', 'draft a', 'template for', 'guide for',
            'explain', 'what does', 'what is', 'define', 'difference between',
            'improve my', 'optimize my', 'when should i', 'why should',
            'interview questions', 'how to evaluate', 'red flags',
            'salary range', 'market rate', 'compensation for',
            'what questions', 'should i ask', 'how to interview',
            'how to assess', 'screening tips', 'evaluation criteria',
            'what to look for', 'hiring tips', 'recruitment tips',
        ]
        if any(sig in msg for sig in advice_signals) and not (has_search_signal or has_location_signal or has_skill_signal or has_role_signal):
            return 'advice'

        # Complex multi-criteria prompts → always 'search' (these are job spec prompts)
        criteria_signals = ['criteria', 'mandatory', 'exclude', 'include', 'domain',
                           'industry', 'background', 'do not', 'must have', 'strictly',
                           'acceptable', 'primary', 'secondary', 'ensure', 'prioritize',
                           'require', 'qualified', 'certified', 'proficient', 'fluent']
        criteria_count = sum(1 for sig in criteria_signals if sig in msg)
        if criteria_count >= 2:
            return 'search'
        
        # If any strong search signal (location, seniority, experience, skill, role), route to search
        if has_location_signal or has_seniority or has_experience or has_skill_signal or has_role_signal:
            return 'search'

        # Default: candidate search
        return 'search'

    async def chat(
        self,
        message: str,
        context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
        candidates_data: Optional[List[Dict]] = None,
        return_candidates: bool = False,
        num_candidates: int = 15,
    ) -> Union[str, Dict]:
        """AI chat assistant with intelligent 2-stage database search.
        
        Stage 0: Classify query type → route to best prompt strategy
        Stage 1: Pre-filter candidates using keyword extraction from user query
        Stage 2: Send relevant subset to Gemini for intelligent analysis
        
        Query types:
        - 'search': Candidate search (full pre-filter + candidate context)
        - 'analytics': Database statistics & insights
        - 'advice': Recruitment best practices & general knowledge
        - 'comparison': Candidate comparison
        - 'greeting': Friendly greeting
        - 'followup': Conversational follow-up
        
        This allows searching the ENTIRE database cost-effectively.
        
        If return_candidates=True, returns a dict with 'response' and 'candidates_lookup'
        mapping [N] indices to candidate data from the database.
        """
        ctx = context or {}
        total = ctx.get('totalCandidates', 0)
        avg_score = ctx.get('avgMatchScore', 0)
        strong = ctx.get('strongMatches', 0)
        categories = ctx.get('categories', {})
        
        # ── STAGE 0: Query Classification ──
        query_type = self._classify_query(message)
        logger.info(f"🧠 Query classified as: {query_type} | Message: {message[:80]}")
        
        # ── Search Cache Check — instant results for repeated queries ──
        if query_type == 'search' and return_candidates:
            cached_result = self._get_search_cached(message, num_candidates, total)
            if cached_result is not None:
                return cached_result

        # Track selected candidates for returning alongside response
        _selected_candidates = []

        # --- STAGE 1: Intelligent Pre-filtering ---
        # Extract keywords from user query for candidate pre-filtering
        query_lower = message.lower()
        
        # Build category summary for general/analytics questions
        cat_summary = ""
        if categories:
            cat_lines = [f"  \u2022 {cat}: {info.get('count', info) if isinstance(info, dict) else info} candidates" for cat, info in sorted(categories.items(), key=lambda x: x[1].get('count', 0) if isinstance(x[1], dict) else x[1], reverse=True)[:15]]
            cat_summary = "\nCategories breakdown:\n" + "\n".join(cat_lines)

        candidates_context = ""
        relevant_count = 0
        total_scanned = 0
        
        # Initialize variables used later in prompt building (set before the candidates_data block)
        has_location_requirement = False
        required_location_terms: list = []
        expanded_location_terms: list = []
        required_min_experience = 0
        required_max_experience = 999  # No upper limit by default
        negative_terms: set = set()
        required_seniority: Optional[str] = None
        detected_roles: list = []
        detected_industries: list = []
        or_alternatives: list = []
        query_phrases: set = set()
        
        # ── Server-side count extraction from message (override frontend default) ──
        _count_match = COUNT_PATTERN.search(message)
        if _count_match:
            extracted_count = int(next(g for g in _count_match.groups() if g))
            if 1 <= extracted_count <= 50:
                num_candidates = extracted_count
                logger.info(f"Extracted requested count from message: {num_candidates}")
        
        if candidates_data:
            total_scanned = len(candidates_data)
            
            # ══════════════════════════════════════════════════════════
            # INTELLIGENT QUERY UNDERSTANDING ENGINE
            # ══════════════════════════════════════════════════════════
            # Step 1: Extract raw keywords
            # Step 2: Detect roles, skills, industries, and modifiers
            # Step 3: Build weighted keyword map (role > skill > generic)
            # Step 4: Detect query structure (OR alternatives, phrases, negations)
            # Step 5: Score candidates with weighted multi-signal matching
            
            scored_candidates = []
            query_tokens = set(re.sub(r'[^\w\s]', ' ', query_lower).split())
            keywords = query_tokens - STOP_WORDS
            
            # ── IT/it case-sensitive disambiguation ──
            # "IT" (uppercase in original) = Information Technology industry
            # "it" (lowercase/pronoun) = noise, already in STOP_WORDS
            original_words = message.split()
            has_explicit_IT = any(w == 'IT' for w in original_words)
            # Also detect "IT" in context: "IT service", "IT company", "IT sector"
            has_IT_context = bool(re.search(r'\bIT\s+(?:service|company|sector|industry|firm|consulting|solutions|infrastructure|support|operations)', message))
            
            if has_explicit_IT or has_IT_context:
                # Re-add 'it' as a keyword since user meant Information Technology
                keywords.add('it')
                logger.info("IT (Information Technology) detected from case/context — added to keywords")
            
            # ── Detect explicit location requirement using shared helpers ──
            required_location_terms = _extract_location_from_text(message)
            expanded_location_terms = _expand_location_terms(required_location_terms)
            has_location_requirement = len(expanded_location_terms) > 0
            
            # ── Detect experience requirement (both min and range/max) ──
            _range_match = EXPERIENCE_RANGE_PATTERN.search(message)
            if _range_match:
                groups = _range_match.groups()
                if groups[0] and groups[1]:       # "0 to 2 years", "1-5 years"
                    required_min_experience = int(groups[0])
                    required_max_experience = int(groups[1])
                elif groups[2] and groups[3]:     # "between 2 and 5 years", "between 1 year to 5 years"
                    required_min_experience = int(groups[2])
                    required_max_experience = int(groups[3])
                elif groups[4] and groups[5]:     # "experience of 1 to 5 years", "experience from 2 to 8 years"
                    required_min_experience = int(groups[4])
                    required_max_experience = int(groups[5])
                elif groups[6]:                   # "max 2 years", "under 3 years", "up to 5 years"
                    required_max_experience = int(groups[6])
                elif groups[7]:                   # "do not include more than 2 years"
                    required_max_experience = int(groups[7])
                elif groups[8]:                   # "must not exceed 3 years"
                    required_max_experience = int(groups[8])
                elif groups[9]:                   # "experience of less than 3 years"
                    required_max_experience = int(groups[9])
                elif groups[10] and groups[11]:   # "only 0-2 years", "strictly 1-3 years"
                    required_min_experience = int(groups[10])
                    required_max_experience = int(groups[11])
                logger.info(f"Experience range detected: {required_min_experience}-{required_max_experience} years")
                # Ensure min <= max
                if required_min_experience > required_max_experience:
                    required_min_experience, required_max_experience = required_max_experience, required_min_experience
            else:
                _exp_match = EXPERIENCE_PATTERN.search(message)
                required_min_experience = int(_exp_match.group(1)) if _exp_match else 0
            
            # Detect fresher/zero experience requirement
            if FRESHER_PATTERN.search(message):
                if required_max_experience == 999:  # Only set if not already set by range
                    required_max_experience = 2  # Freshers = max 2 years
                    required_min_experience = 0
                    logger.info("Fresher/entry-level detected — setting experience range 0-2")
            
            # ── Extract negative/exclusion keywords ──
            negative_terms: set = set()
            _neg_match = NEGATIVE_PATTERN.search(message)
            if _neg_match:
                neg_raw = _neg_match.group(1).strip().lower()
                for part in re.split(r'[,;&]+|\s+or\s+', neg_raw):
                    part = part.strip()
                    part = re.sub(r'\s+(background|experience|skills?|developers?|engineers?|knowledge|expertise)$', '', part)
                    if part and part not in STOP_WORDS and len(part) > 1:
                        negative_terms.add(part)
                if negative_terms:
                    logger.info(f"Exclusion terms detected: {negative_terms}")
            
            # ── Detect required seniority level ──
            _seniority_match = SENIORITY_PATTERN.search(message)
            required_seniority = _seniority_match.group(1).lower().strip() if _seniority_match else None
            
            # ══════════════════════════════════════════════════════════
            # ROLE DETECTION — Identify job roles in the query
            # ══════════════════════════════════════════════════════════
            detected_roles = []
            query_clean_for_roles = re.sub(r'[^\w\s]', ' ', query_lower)
            # Check multi-word role titles first (longest match wins)
            for role in sorted(ROLE_TITLES, key=len, reverse=True):
                if ' ' in role and role in query_clean_for_roles:
                    detected_roles.append(role)
                    # Remove from query to avoid double-matching individual words
                    query_clean_for_roles = query_clean_for_roles.replace(role, ' ')
            # Then check single-word role titles
            remaining_words = set(query_clean_for_roles.split()) - STOP_WORDS
            for role in ROLE_TITLES:
                if ' ' not in role and role in remaining_words:
                    detected_roles.append(role)
            if detected_roles:
                logger.info(f"🎯 Detected roles: {detected_roles}")
            
            # ══════════════════════════════════════════════════════════
            # INDUSTRY DETECTION — Identify industry/domain context
            # ══════════════════════════════════════════════════════════
            detected_industries = []
            for ind_key, ind_syns in INDUSTRY_KEYWORDS.items():
                # Check the keyword itself
                if ind_key in query_lower:
                    detected_industries.append(ind_key)
                    continue
                # Check multi-word variants
                for syn in ind_syns:
                    if syn in query_lower:
                        detected_industries.append(ind_key)
                        break
            # Special: IT detection from uppercase
            if (has_explicit_IT or has_IT_context) and 'it' not in detected_industries:
                detected_industries.append('it')
            if detected_industries:
                logger.info(f"🏢 Detected industries: {detected_industries}")
            
            # Build industry expansion terms for matching
            industry_match_terms = set()
            for ind in detected_industries:
                industry_match_terms.add(ind)
                if ind in INDUSTRY_KEYWORDS:
                    industry_match_terms.update(INDUSTRY_KEYWORDS[ind])
            
            # ══════════════════════════════════════════════════════════
            # WEIGHTED KEYWORD MAP — Assign importance weights
            # ══════════════════════════════════════════════════════════
            # role_keywords: highest weight (what job they're looking for)
            # skill_keywords: high weight (technical requirements)
            # industry_keywords: medium weight (domain context)
            # generic_keywords: lower weight (general terms)
            role_keywords = set()
            skill_keywords = set()
            generic_keywords = set()
            
            for kw in keywords:
                # Skip location terms — they're handled separately
                if has_location_requirement and kw in expanded_location_terms:
                    continue
                # Check if it's a known role
                if kw in ROLE_TITLES or any(kw in role for role in detected_roles):
                    role_keywords.add(kw)
                # Check if it's a known skill/technology
                elif kw in SKILL_SYNONYMS:
                    skill_keywords.add(kw)
                else:
                    generic_keywords.add(kw)
            
            # Also add detected multi-word roles as skill_keywords for matching
            for role in detected_roles:
                for word in role.split():
                    if word not in STOP_WORDS and len(word) >= 2:
                        role_keywords.add(word)
            
            # Expand keywords with location aliases
            expanded_keywords = set(keywords)
            for alias, expansions in LOCATION_ALIASES.items():
                if alias in keywords:
                    expanded_keywords.update(expansions)
            
            # Expand keywords with skill synonyms for broader pre-filter recall
            # BUT: don't expand broad industry terms to avoid drowning signal
            BROAD_EXPANSION_SKIP = {'cloud', 'api', 'database', 'frontend', 'backend',
                                    'fullstack', 'full stack', 'security', 'qa', 'testing'}
            # Also skip expanding 'it' unless it was explicitly detected as IT (industry)
            if not (has_explicit_IT or has_IT_context):
                BROAD_EXPANSION_SKIP.add('it')
            synonym_expanded = set()
            for kw in list(expanded_keywords):
                if kw in BROAD_EXPANSION_SKIP:
                    continue
                syns = SKILL_SYNONYMS.get(kw, set())
                if syns:
                    for syn in syns:
                        if len(syn) >= 2:
                            synonym_expanded.add(syn)
            expanded_keywords.update(synonym_expanded)
            
            # ── Detect OR-separated role alternatives ──
            # "sales or account manager" → ["sales", "account manager"] as alternative roles
            or_alternatives = []
            or_pattern = re.compile(
                r'\b([\w]+(?:\s+[\w]+)?)\s+or\s+([\w]+(?:\s+[\w]+){0,2})\b',
                re.IGNORECASE
            )
            for m in or_pattern.finditer(query_lower):
                alt_a = m.group(1).strip()
                alt_b = m.group(2).strip()
                # Filter out stop words and locations from alternatives
                if alt_a not in STOP_WORDS and alt_a not in KNOWN_LOCATIONS:
                    or_alternatives.append(alt_a)
                if alt_b not in STOP_WORDS and alt_b not in KNOWN_LOCATIONS:
                    or_alternatives.append(alt_b)
            if or_alternatives:
                logger.info(f"OR-alternatives detected: {or_alternatives}")
                    
            # ── Build multi-word phrases from query for phrase matching ──
            query_clean = re.sub(r'[^\w\s]', ' ', query_lower)
            query_words_all = query_clean.split()
            query_words_ordered = [w for w in query_words_all if w not in STOP_WORDS and len(w) >= 2]
            query_phrases = set()
            # 2-word phrases from non-stop words
            for pi in range(len(query_words_ordered) - 1):
                phrase = f"{query_words_ordered[pi]} {query_words_ordered[pi+1]}"
                if phrase in query_lower:
                    query_phrases.add(phrase)
            # 2-word and 3-word phrases from ALL words (catches "account manager", "it service")
            for pi in range(len(query_words_all) - 1):
                p2 = f"{query_words_all[pi]} {query_words_all[pi+1]}"
                if p2 in query_lower and any(w not in STOP_WORDS for w in [query_words_all[pi], query_words_all[pi+1]]):
                    query_phrases.add(p2)
            for pi in range(len(query_words_all) - 2):
                p3 = f"{query_words_all[pi]} {query_words_all[pi+1]} {query_words_all[pi+2]}"
                if p3 in query_lower and sum(1 for w in p3.split() if w not in STOP_WORDS) >= 2:
                    query_phrases.add(p3)
            # Add OR alternatives and detected roles as high-priority phrases
            query_phrases.update(or_alternatives)
            query_phrases.update(detected_roles)
            if query_phrases:
                logger.info(f"Phrase matching active: {query_phrases}")
            
            # Log the full query analysis
            logger.info(
                f"🧠 Query Analysis: roles={detected_roles}, skills={list(skill_keywords)[:6]}, "
                f"industries={detected_industries}, generic={list(generic_keywords)[:4]}, "
                f"or_alts={or_alternatives}, phrases={list(query_phrases)[:6]}"
            )
            
            for idx, c in enumerate(candidates_data):
                relevance = 0
                name = str(c.get('name', '')).lower()
                skills = [s.lower() for s in c.get('skills', [])]
                skills_str = ' '.join(skills)
                category = (c.get('jobCategory') or c.get('job_category') or '').lower()
                subcategory = (c.get('jobSubcategory') or c.get('job_subcategory') or '').lower()
                # Also match against normalized category words for broader matching
                cat_words = set(re.sub(r'[^\w\s]', ' ', category).split()) | set(re.sub(r'[^\w\s]', ' ', subcategory).split())
                location = (c.get('location') or '').lower()
                summary = (c.get('summary') or '').lower()
                experience = _safe_int_experience(c.get('experience', 0))
                score = c.get('matchScore', c.get('match_score', 0)) or 0
                
                # ── Build searchable text from work history ──
                work_history = c.get('workHistory', c.get('work_history', []))
                wh_titles = []
                wh_companies = []
                wh_full_text = ''
                if isinstance(work_history, list):
                    for w in work_history:
                        if isinstance(w, dict):
                            t = str(w.get('title', '')).lower()
                            comp = str(w.get('company', '')).lower()
                            if t: wh_titles.append(t)
                            if comp: wh_companies.append(comp)
                    wh_full_text = ' '.join(wh_titles + wh_companies)
                
                # ── Build searchable text from education ──
                education = c.get('education', [])
                edu_text = ''
                if isinstance(education, list):
                    for e in education:
                        if isinstance(e, dict):
                            edu_text += f" {str(e.get('degree', '')).lower()} {str(e.get('institution', '')).lower()} {str(e.get('field', '')).lower()}"
                edu_text = edu_text.strip()
                
                # ── Build searchable text from certifications ──
                certs = c.get('certifications', [])
                certs_text = ' '.join([str(x).lower() for x in certs[:10]]) if isinstance(certs, list) else ''
                
                # ── Build searchable text from resume_text (truncated for speed) ──
                resume_text = str(c.get('resume_text', '') or '')[:2000].lower()
                
                # ── Job applied for ──
                job_applied_for = str(c.get('job_applied_for', c.get('jobAppliedFor', '')) or '').lower()
                
                # ── Languages ──
                langs = c.get('languages', [])
                langs_text = ' '.join([str(x).lower() for x in langs[:10]]) if isinstance(langs, list) else ''
                
                # ── HARD FILTER: Experience cap ──
                # When user specifies an explicit max (e.g. "0-2 years"), completely
                # exclude candidates that exceed it — this is non-negotiable.
                if required_max_experience < 999 and experience > required_max_experience:
                    continue  # Skip this candidate entirely
                if required_min_experience > 0 and experience < required_min_experience:
                    continue  # Below minimum experience — skip
                
                # ── HARD FILTER: Negative keywords ──
                # If user says "exclude sales" or "not marketing", skip candidates matching those
                if negative_terms:
                    candidate_text = f"{name} {skills_str} {category} {subcategory} {wh_full_text}".lower()
                    neg_hit = False
                    for neg in negative_terms:
                        # Use word boundary matching to avoid false positives
                        # e.g. "marketing" should not match "supermarket"
                        if re.search(r'\b' + re.escape(neg) + r'\b', candidate_text):
                            neg_hit = True
                            break
                    if neg_hit:
                        continue  # Skip excluded candidates
                
                # ── Seniority level scoring ──
                if required_seniority:
                    seniority_fit = 0
                    if required_seniority in ('junior', 'entry-level', 'entry level', 'fresher', 'fresh graduate', 'intern', 'trainee', 'beginner'):
                        if experience <= 2:
                            seniority_fit = 25
                        elif experience <= 3:
                            seniority_fit = 10
                        else:
                            seniority_fit = -20  # Overqualified
                    elif required_seniority in ('mid-level', 'mid level', 'midlevel'):
                        if 3 <= experience <= 7:
                            seniority_fit = 25
                        elif 2 <= experience <= 9:
                            seniority_fit = 10
                        else:
                            seniority_fit = -15
                    elif required_seniority in ('senior', 'lead', 'principal', 'staff'):
                        if experience >= 7:
                            seniority_fit = 25
                        elif experience >= 5:
                            seniority_fit = 15
                        else:
                            seniority_fit = -20  # Under-qualified
                    elif required_seniority in ('director', 'head', 'vp', 'c-level', 'chief', 'executive'):
                        if experience >= 12:
                            seniority_fit = 25
                        elif experience >= 8:
                            seniority_fit = 10
                        else:
                            seniority_fit = -25
                    elif required_seniority == 'manager':
                        if experience >= 5:
                            seniority_fit = 20
                        elif experience >= 3:
                            seniority_fit = 10
                        else:
                            seniority_fit = -15
                    relevance += seniority_fit
                
                # ── Location-aware scoring (tiered: exact city > same country > region) ──
                location_matched = False
                location_exact_city = False
                if has_location_requirement:
                    loc_words = set(re.sub(r'[^\w\s]', ' ', location).split())
                    # Check for exact city/term match first (highest priority)
                    for lt in required_location_terms:
                        if lt in loc_words or (len(lt) > 3 and lt in location):
                            location_matched = True
                            location_exact_city = True
                            break
                    # If not exact, check expanded aliases (same country/region)
                    if not location_matched:
                        for lt in expanded_location_terms:
                            if lt in loc_words or (len(lt) > 3 and lt in location):
                                location_matched = True
                                break
                    if location_exact_city:
                        relevance += 80  # Exact city/term match — strongest boost
                    elif location_matched:
                        relevance += 45  # Same country/region match — good but not perfect
                    else:
                        relevance -= 50  # Wrong location — strong penalty when location is required
                
                # ── Nationality scoring ──
                _nat_match = NATIONALITY_PATTERN.search(message)
                if _nat_match:
                    required_nationality = _nat_match.group(1).strip().lower()
                    candidate_nationality = str(c.get('nationality', '')).lower()
                    if candidate_nationality and required_nationality:
                        # Check if any word in required nationality matches candidate's nationality
                        nat_words = set(required_nationality.split())
                        if any(nw in candidate_nationality for nw in nat_words if len(nw) > 2):
                            relevance += 50  # Strong nationality match
                        elif required_nationality in candidate_nationality or candidate_nationality in required_nationality:
                            relevance += 50
                        else:
                            relevance -= 20  # Nationality mismatch when explicitly requested
                
                # ── Multi-word phrase matching (bonus on top of individual keyword scores) ──
                # Phrases and OR-alternatives carry MUCH higher weight than individual keywords
                all_candidate_text = f"{skills_str} {wh_full_text} {summary} {category} {subcategory} {job_applied_for} {edu_text} {certs_text} {resume_text[:500]}"
                or_alt_matched = False
                role_phrase_matched = False
                for phrase in query_phrases:
                    is_or_alt = phrase in or_alternatives
                    is_role = phrase in detected_roles
                    # Role phrases and OR alternatives get massive boost
                    if is_or_alt:
                        phrase_boost = 35
                    elif is_role:
                        phrase_boost = 30
                    else:
                        phrase_boost = 15
                    
                    if phrase in skills_str:
                        relevance += phrase_boost
                        if is_or_alt: or_alt_matched = True
                        if is_role: role_phrase_matched = True
                    if phrase in wh_full_text:
                        relevance += phrase_boost
                        if is_or_alt: or_alt_matched = True
                        if is_role: role_phrase_matched = True
                    if phrase in summary:
                        relevance += int(phrase_boost * 0.7)
                        if is_or_alt: or_alt_matched = True
                        if is_role: role_phrase_matched = True
                    if phrase in category or phrase in subcategory:
                        relevance += phrase_boost
                        if is_or_alt: or_alt_matched = True
                        if is_role: role_phrase_matched = True
                    if phrase in job_applied_for:
                        relevance += phrase_boost
                        if is_or_alt: or_alt_matched = True
                        if is_role: role_phrase_matched = True
                    if phrase in edu_text:
                        relevance += 8
                    if phrase in certs_text:
                        relevance += 10
                    # Check resume text for phrase match too (weaker)
                    if phrase in resume_text:
                        relevance += 8
                        if is_or_alt: or_alt_matched = True
                        if is_role: role_phrase_matched = True
                
                # Penalties for missing critical signals
                if or_alternatives and not or_alt_matched:
                    relevance -= 25  # User gave OR alternatives but NONE matched
                if detected_roles and not role_phrase_matched:
                    # Check single-word role matches as fallback
                    any_role_word = False
                    for role in detected_roles:
                        for rw in role.split():
                            if rw in all_candidate_text:
                                any_role_word = True
                                break
                    if not any_role_word:
                        relevance -= 30  # Completely wrong role — heavy penalty
                
                # ── Industry/domain matching ──
                # When user mentions industry (IT service, banking, healthcare), check
                # work history companies AND candidate category/summary
                if detected_industries:
                    industry_score = 0
                    comp_text = ' '.join(wh_companies)
                    combined_text = f"{comp_text} {category} {subcategory} {summary}"
                    for term in industry_match_terms:
                        if len(term) >= 2 and term in combined_text:
                            industry_score += 8
                    # Cap industry bonus at 30
                    relevance += min(industry_score, 30)
                    # Penalty if industry specified but no match in company names
                    if industry_score == 0 and comp_text:
                        relevance -= 10  # Not in the right industry
                
                # ── Job title matching — HIGHEST value signal ──
                # Check if work history titles match detected roles or OR alternatives
                role_title_bonus = 0
                for title in wh_titles:
                    title_words = set(title.split())
                    # Check against detected roles (multi-word match)
                    for role in detected_roles:
                        if role in title:
                            role_title_bonus = max(role_title_bonus, 40)  # Exact role in title
                    # Check OR alternatives against titles
                    for alt in or_alternatives:
                        if alt in title:
                            role_title_bonus = max(role_title_bonus, 40)
                    # Check expanded keyword overlap with title
                    title_kw_overlap = len(title_words & expanded_keywords)
                    role_kw_overlap = len(title_words & role_keywords)
                    if role_kw_overlap >= 2:
                        role_title_bonus = max(role_title_bonus, 35)
                    elif role_kw_overlap == 1:
                        role_title_bonus = max(role_title_bonus, 20)
                    elif title_kw_overlap >= 2:
                        role_title_bonus = max(role_title_bonus, 25)
                    elif title_kw_overlap == 1 and any(kw in title for kw in expanded_keywords if len(kw) >= 4):
                        role_title_bonus = max(role_title_bonus, 15)
                relevance += role_title_bonus
                
                # ── WEIGHTED keyword scoring ──
                # Role keywords (25 pts match / -8 pts miss)
                # Skill keywords (20 pts match / -5 pts miss)  
                # Generic keywords (10 pts match / -3 pts miss)
                for kw in expanded_keywords:
                    if len(kw) < 2:
                        continue
                    # Skip location keywords — handled separately
                    if has_location_requirement and kw in expanded_location_terms:
                        continue
                    
                    # Determine weight tier
                    if kw in role_keywords:
                        hit_weight = 25
                        miss_penalty = -8
                    elif kw in skill_keywords or kw in SKILL_SYNONYMS:
                        hit_weight = 20
                        miss_penalty = -5
                    else:
                        hit_weight = 10
                        miss_penalty = -3
                    
                    kw_synonyms = SKILL_SYNONYMS.get(kw, set())
                    matched_anywhere = False
                    
                    # Skills check (highest signal)
                    for s in skills:
                        s_words = set(re.sub(r'[^\w\s]', ' ', s).split())
                        if kw in s_words or kw == s:
                            relevance += hit_weight
                            matched_anywhere = True
                            break
                        if kw_synonyms and (kw_synonyms & s_words or s in kw_synonyms):
                            relevance += int(hit_weight * 0.9)
                            matched_anywhere = True
                            break
                    
                    # Category/subcategory
                    if kw in cat_words:
                        relevance += int(hit_weight * 0.75)
                        matched_anywhere = True
                    elif kw_synonyms and (kw_synonyms & cat_words):
                        relevance += int(hit_weight * 0.6)
                        matched_anywhere = True
                    
                    # Direct name search (always high)
                    if kw in name.split():
                        relevance += 25
                        matched_anywhere = True
                    
                    # Work history (titles + companies)
                    wh_words = set(wh_full_text.split())
                    if kw in wh_words:
                        relevance += int(hit_weight * 0.75)
                        matched_anywhere = True
                    elif kw_synonyms and (kw_synonyms & wh_words):
                        relevance += int(hit_weight * 0.6)
                        matched_anywhere = True
                    elif not matched_anywhere:
                        # Substring check in titles (e.g. "sales" in "sales manager")
                        for title in wh_titles:
                            if kw in title:
                                relevance += int(hit_weight * 0.5)
                                matched_anywhere = True
                                break
                    
                    # Job applied for (strong signal)
                    if job_applied_for and kw in set(job_applied_for.split()):
                        relevance += int(hit_weight * 0.9)
                        matched_anywhere = True
                    
                    # Summary
                    if kw in set(summary.split()):
                        relevance += int(hit_weight * 0.5)
                        matched_anywhere = True
                    
                    # Location keyword (only when no explicit location requirement)
                    if not has_location_requirement and kw in location:
                        relevance += 15
                        matched_anywhere = True
                    
                    # Education
                    if edu_text and kw in set(edu_text.split()):
                        relevance += 8
                        matched_anywhere = True
                    
                    # Certifications
                    if certs_text and kw in set(certs_text.split()):
                        relevance += 10
                        matched_anywhere = True
                    
                    # Resume text (fallback)
                    if not matched_anywhere and resume_text and kw in set(resume_text.split()):
                        relevance += 6
                        matched_anywhere = True
                    
                    # Language match
                    if langs_text and kw in set(langs_text.split()):
                        relevance += 15
                        matched_anywhere = True
                    
                    # Miss penalty — keyword found NOWHERE
                    if not matched_anywhere:
                        relevance += miss_penalty
                
                # Boost by match score
                relevance += score * 0.15
                
                # ── Experience requirement check — bonus for fitting the range ──
                # (Hard exclusion already happened above via `continue`)
                if required_max_experience < 999:
                    # Candidate is within range (hard filter already excluded outliers)
                    if experience >= required_min_experience and experience <= required_max_experience:
                        relevance += 30  # Perfect fit within range
                elif required_min_experience > 0:
                    if experience >= required_min_experience:
                        relevance += 20  # Meets minimum experience
                        if experience >= required_min_experience * 1.5:
                            relevance += 10
                    else:
                        relevance -= 25  # Below minimum experience
                else:
                    # General experience boost even when no explicit requirement
                    relevance += min(experience, 15) * 1.0
                
                # Recency boost — only for explicitly recency-related queries
                created_at = c.get('created_at', '')
                if created_at and any(w in query_lower for w in ['new', 'recent', 'latest', 'today', 'week', 'fresh']):
                    try:
                        from datetime import datetime, timedelta
                        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00')) if 'T' in created_at else datetime.strptime(created_at[:19], '%Y-%m-%d %H:%M:%S')
                        now = datetime.utcnow()
                        age_hours = (now - created_dt.replace(tzinfo=None)).total_seconds() / 3600
                        if age_hours <= 24:
                            relevance += 20
                        elif age_hours <= 72:
                            relevance += 15
                        elif age_hours <= 168:
                            relevance += 10
                    except (ValueError, TypeError):
                        pass
                
                # Experience-based queries — seniority scoring (only if no explicit seniority pattern matched)
                if not required_seniority:
                    if any(w in query_lower for w in ['senior', 'experienced', 'lead', 'principal', 'director', 'head', 'vp', 'chief']):
                        if experience >= 10:
                            relevance += 20
                        elif experience >= 7:
                            relevance += 15
                        elif experience >= 5:
                            relevance += 5
                        else:
                            relevance -= 10
                    elif any(w in query_lower for w in ['mid', 'intermediate', 'mid-level', 'moderate']):
                        if 3 <= experience <= 7:
                            relevance += 15
                    elif any(w in query_lower for w in ['junior', 'entry', 'fresher', 'graduate', 'intern', 'trainee', 'beginner']):
                        if experience <= 2:
                            relevance += 15
                        elif experience <= 3:
                            relevance += 5
                        else:
                            relevance -= 10
                
                # ── Notice period / availability scoring ──
                # When user mentions "immediate", "available now", "urgent"
                if any(w in query_lower for w in ['immediate', 'immediately', 'urgent', 'asap', 'available now', 'available immediately', 'quick joiner', 'immediate joiner']):
                    notice = str(c.get('notice_period', '') or '').lower()
                    if notice:
                        if any(term in notice for term in ['immediate', '0 days', 'available', 'ready', 'now', 'serving']):
                            relevance += 25
                        elif any(term in notice for term in ['15 days', '1 week', '2 weeks', 'short']):
                            relevance += 15
                        elif any(term in notice for term in ['30 days', '1 month', 'one month']):
                            relevance += 5
                
                # ── Has resume boost — candidates with resumes have richer data ──
                if c.get('hasResume', c.get('has_resume', False)):
                    relevance += 3  # Small quality boost
                
                scored_candidates.append((relevance, idx, c))
            
            # Log hard-filter exclusion stats
            hard_filtered_out = total_scanned - len(scored_candidates)
            if hard_filtered_out > 0:
                logger.info(f"🚫 Hard filter excluded {hard_filtered_out}/{total_scanned} candidates "
                           f"(exp={required_min_experience}-{required_max_experience}y, "
                           f"neg={negative_terms or 'none'})")
            
            # Sort by relevance and take top candidates (idx as tiebreaker to avoid dict comparison)
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            
            # Dynamic pool size — balance accuracy with speed
            # More candidates = better accuracy, but slower Gemini processing
            has_specific_keywords = len(keywords) >= 2
            has_many_keywords = len(keywords) >= 4
            
            if has_many_keywords:
                MAX_CANDIDATES_TO_GEMINI = 50  # Complex query — pre-filter is strong, wider pool for accuracy
            elif has_specific_keywords:
                MAX_CANDIDATES_TO_GEMINI = 50  # Moderate query — wider pool for better recall
            else:
                MAX_CANDIDATES_TO_GEMINI = 60  # Broad/simple query — widest pool needed
            
            # Ensure we request at least enough for the user's num_candidates
            MAX_CANDIDATES_TO_GEMINI = max(MAX_CANDIDATES_TO_GEMINI, min(num_candidates + 5, 50))
            
            if has_specific_keywords:
                relevant = [(score, idx, c) for score, idx, c in scored_candidates if score > 0]
                selected = relevant[:MAX_CANDIDATES_TO_GEMINI] if relevant else scored_candidates[:50]
            else:
                selected = scored_candidates[:MAX_CANDIDATES_TO_GEMINI]
            
            relevant_count = len(selected)
            
            # ── Diagnostic logging ──
            top5_scores = [(c.get('name', '?'), s) for s, _, c in selected[:5]]
            logger.info(
                f"Pre-filter: {total_scanned} candidates → {relevant_count} selected "
                f"(MAX={MAX_CANDIDATES_TO_GEMINI}, roles={detected_roles[:3]}, "
                f"skills={list(skill_keywords)[:5]}, industries={detected_industries[:2]}, "
                f"location={'yes' if has_location_requirement else 'no'}, "
                f"exp={required_min_experience}-{required_max_experience}y, "
                f"seniority={required_seniority or 'any'}, "
                f"neg={list(negative_terms)[:3] if negative_terms else 'none'}, "
                f"or_alts={or_alternatives[:3]}, phrases={list(query_phrases)[:4]}) "
                f"Top5: {top5_scores}"
            )
            
            # Store selected candidates for frontend matching
            _selected_candidates = [c for (_score, _idx, c) in selected[:MAX_CANDIDATES_TO_GEMINI]]
            
            # Build context — rich format for accurate matching
            # Each candidate includes summary for deeper Gemini analysis
            candidates_context = f"\n\nCANDIDATES ({relevant_count} pre-filtered from {total_scanned}):\n"
            for i, (rel_score, _idx, c) in enumerate(selected[:MAX_CANDIDATES_TO_GEMINI]):
                skills_raw = c.get('skills', [])
                skills_str = ', '.join(skills_raw[:12]) if isinstance(skills_raw, list) else str(skills_raw or '')
                work = c.get('workHistory', c.get('work_history', []))
                if isinstance(work, list):
                    work_entries = []
                    for w in work[:3]:  # Top 3 positions, no descriptions
                        if isinstance(w, dict):
                            entry = f"{w.get('title', 'N/A')} @ {w.get('company', 'N/A')}"
                            dur = w.get('duration', w.get('period', ''))
                            if dur:
                                entry += f" ({dur})"
                            work_entries.append(entry)
                    work_str = '; '.join(work_entries) or 'N/A'
                else:
                    work_str = str(work)[:150] if work else 'N/A'
                edu = c.get('education', [])
                edu_str = 'N/A'
                if isinstance(edu, list) and edu:
                    top_edu = edu[0]
                    if isinstance(top_edu, dict):
                        edu_str = ' - '.join(p for p in [top_edu.get('degree', ''), top_edu.get('field', ''), top_edu.get('institution', '')] if p) or 'N/A'
                
                candidates_context += (
                    f"[{i+1}] {c.get('name', 'Unknown')} | {c.get('matchScore', 0)}% | "
                    f"{c.get('jobCategory', c.get('job_category', 'General'))} | "
                    f"Exp: {c.get('experience', 0)}yrs | {c.get('location', 'N/A')}\n"
                    f"   Skills: {skills_str}\n"
                    f"   Work: {work_str}\n"
                    f"   Edu: {edu_str}\n"
                    f"   Contact: {c.get('email', 'N/A')} | {c.get('phone', 'N/A')}\n"
                )
                # Add summary/profile for deeper analysis
                _summary = c.get('summary', c.get('profile_summary', ''))
                if _summary:
                    candidates_context += f"   Profile: {str(_summary)[:150]}\n"
                # Compact extras — only high-value fields on one line
                _extras = []
                _nat = c.get('nationality', '')
                _notice = c.get('notice_period', '')
                _job_app = c.get('job_applied_for', '')
                _langs = c.get('languages', [])
                if _nat: _extras.append(f"Nat: {_nat}")
                if _notice: _extras.append(f"Notice: {_notice}")
                if _job_app: _extras.append(f"Applied: {_job_app}")
                if isinstance(_langs, list) and _langs: _extras.append(f"Lang: {', '.join(str(x) for x in _langs[:3])}")
                if _extras:
                    candidates_context += f"   {' | '.join(_extras)}\n"

        # Build conversation context — keep minimal for speed
        history_text = ""
        if conversation_history:
            # Keep last 4 messages for search, 8 for other types
            hist_limit = 4 if query_type == 'search' else 8
            for msg in conversation_history[-hist_limit:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')[:400]
                history_text += f"\n{role}: {content}"

        cat_list = ', '.join([f"{k}: {v}" for k, v in list(categories.items())[:12]]) if categories else 'N/A'

        # Build dynamic constraint sections
        constraints = []
        if detected_roles:
            constraints.append(f"ROLE: Looking for {', '.join(detected_roles)} — rank matching titles highest, exclude wrong roles.")
        if or_alternatives:
            constraints.append(f"OR-ALTERNATIVES: Accept ANY of: {', '.join(or_alternatives)}")
        if detected_industries:
            constraints.append(f"INDUSTRY: {', '.join(detected_industries)} sector — prioritize matching work history.")
        if has_location_requirement:
            loc_str = ', '.join(required_location_terms)
            constraints.append(f"LOCATION (MANDATORY): {loc_str} — rank local first, flag non-local with ⚠️.")
        if required_max_experience < 999:
            constraints.append(f"EXPERIENCE (STRICT): {required_min_experience}-{required_max_experience} years only. Hard filter.")
        elif required_min_experience > 0:
            constraints.append(f"EXPERIENCE: {required_min_experience}+ years minimum.")
        if negative_terms:
            constraints.append(f"EXCLUDE (STRICT): {', '.join(negative_terms)} — zero tolerance.")
        if required_seniority:
            constraints.append(f"SENIORITY: Target '{required_seniority}' level.")
        
        constraints_text = "\n".join(f"• {c}" for c in constraints) if constraints else "No special filters."

        # ══════════════════════════════════════════════════════════════
        # STAGE 2: Build prompt based on query type
        # ══════════════════════════════════════════════════════════════

        if query_type == 'greeting':
            # ── Fast greeting — no Gemini call needed ──
            greetings_map = {
                'hi': 'Hello! 👋', 'hello': 'Hello! 👋', 'hey': 'Hey there! 👋',
                'good morning': 'Good morning! ☀️', 'good afternoon': 'Good afternoon! 🌤️',
                'good evening': 'Good evening! 🌙',
                'thanks': 'You\'re welcome! 😊', 'thank you': 'You\'re welcome! 😊',
                'ok': 'Great!', 'okay': 'Great!',
            }
            greeting = greetings_map.get(message.lower().strip(), 'Hello! 👋')
            text_response = f"""{greeting} I'm your AI Recruitment Assistant for Efforts Solutions.

**Here's what I can do:**

🔍 **Find Candidates** — "Find React developers in Dubai with 3+ years experience"
📊 **Analytics** — "How many candidates do we have by category?"
💡 **Recruitment Advice** — "How to evaluate a senior backend engineer?"
📋 **Compare Candidates** — "Compare John and Sarah for the PM role"
📝 **Interview Help** — "What questions should I ask a data scientist?"

**Quick Stats:** {total} candidates in database | {strong} strong matches (70%+)
{cat_summary}

What would you like to explore?"""

        elif query_type == 'advice':
            # ── Recruitment expertise prompt — no candidate data needed ──
            prompt = f"""Senior Recruitment Strategist for Efforts Solutions (20+ yrs experience across tech, finance, healthcare, engineering, executive hiring). Expert in: talent acquisition, interview design, compensation, employer branding, DEI, ATS, labor markets (GCC/India/US/UK/EU).

DATABASE: {total} candidates | Categories: {cat_list}

HISTORY:{history_text}

QUESTION: {message}

Respond with actionable, expert advice. Use headers, bullets, examples. Reference industry standards. Include pro tips and pitfalls. End with specific next step. Markdown format."""

            result = await self._agenerate(prompt, temperature=0.3, max_tokens=4000, thinking_budget=2048)
            text_response = result or "I'd be happy to help with recruitment advice. Could you provide more details about your question?"

        elif query_type == 'analytics':
            # ── Analytics prompt — uses database stats, minimal candidate data ──
            # Build richer stats context
            stats_detail = f"""DATABASE ANALYTICS:
- Total Candidates: {total}
- Strong Matches (70%+): {strong}
- Average Match Score: {avg_score:.1f}%
{cat_summary}
"""
            # Add top candidates by score if available
            if _selected_candidates:
                top_by_score = sorted(_selected_candidates[:200], key=lambda c: c.get('matchScore', 0), reverse=True)[:10]
                stats_detail += "\nTop 10 Candidates by Score:\n"
                for i, c in enumerate(top_by_score, 1):
                    stats_detail += f"  {i}. {c.get('name', 'N/A')} — {c.get('matchScore', 0)}% | {c.get('jobCategory', 'General')} | {c.get('experience', 0)} yrs | {c.get('location', 'N/A')}\n"
                
                # Location distribution
                loc_counts: Dict[str, int] = {}
                for c in _selected_candidates[:500]:
                    loc = str(c.get('location', 'Unknown')).strip()
                    if loc:
                        loc_counts[loc] = loc_counts.get(loc, 0) + 1
                top_locations = sorted(loc_counts.items(), key=lambda x: x[1], reverse=True)[:10]
                stats_detail += "\nTop Locations:\n" + "\n".join(f"  • {loc}: {cnt}" for loc, cnt in top_locations)
                
                # Experience distribution
                exp_buckets = {'0-2 yrs': 0, '3-5 yrs': 0, '6-10 yrs': 0, '10+ yrs': 0}
                for c in _selected_candidates[:500]:
                    exp = _safe_int_experience(c.get('experience', 0))
                    if exp <= 2: exp_buckets['0-2 yrs'] += 1
                    elif exp <= 5: exp_buckets['3-5 yrs'] += 1
                    elif exp <= 10: exp_buckets['6-10 yrs'] += 1
                    else: exp_buckets['10+ yrs'] += 1
                stats_detail += "\n\nExperience Distribution:\n" + "\n".join(f"  • {k}: {v}" for k, v in exp_buckets.items())

            prompt = f"""Recruitment Analytics Advisor for Efforts Solutions. Analyze data, provide insightful answers with specific numbers.

{stats_detail}

HISTORY:{history_text}

QUESTION: {message}

Lead with numbers. Use tables/bullets. Explain what data means. Highlight trends and gaps. Suggest actionable steps. Use markdown."""

            # Analytics: thinking disabled — data-driven, no complex reasoning needed
            result = await self._agenerate(prompt, temperature=0.15, max_tokens=4000, thinking_budget=0)
            text_response = result or f"We have **{total} candidates** in the database. Could you clarify what analytics you'd like to see?"

        elif query_type == 'followup':
            # ── Conversational follow-up — leverage conversation history ──
            prompt = f"""You are an AI Recruitment Assistant for Efforts Solutions. The user is continuing a conversation.

DATABASE: {total} candidates | Categories: {cat_list}

CONVERSATION HISTORY:{history_text}

USER FOLLOW-UP:
{message}

{f"CANDIDATE POOL (if needed for follow-up):{candidates_context}" if candidates_context else ""}

Respond naturally to the follow-up. If they're asking for more candidates, different criteria, or clarification, provide it. If they're acknowledging or confirming, respond appropriately.
Keep the same format and quality as the previous response. Use markdown formatting."""

            result = await self._agenerate(prompt, temperature=0.2, max_tokens=4000, thinking_budget=0)
            text_response = result or "Could you provide more details about what you'd like me to do next?"

        else:
            # ══════════════════════════════════════════════════════════
            # CANDIDATE SEARCH — precision-first, sub-5s response
            # ══════════════════════════════════════════════════════════

            prompt = f"""You are the AI Recruiter for Efforts Solutions. Your job: return EXACTLY the right candidates — no padding, no guessing.

═══ DATABASE SNAPSHOT ═══
Total: {total} candidates | Strong (70%+): {strong} | Avg Score: {avg_score:.0f}% | Categories: {cat_list}

═══ ACTIVE FILTERS (HARD CONSTRAINTS) ═══
{constraints_text}

═══ QUERY INTELLIGENCE ═══
Detected Roles: {', '.join(detected_roles) if detected_roles else 'any/unspecified'}
OR-Alternatives: {', '.join(or_alternatives) if or_alternatives else 'none — AND-logic applies'}
Industries: {', '.join(detected_industries) if detected_industries else 'any'}
Keywords: {', '.join(sorted(query_tokens - STOP_WORDS)[:20]) if query_tokens else 'see query'}

═══ CANDIDATE POOL ({relevant_count} pre-scored from {total_scanned}) ═══
{candidates_context}

═══ CONVERSATION HISTORY ═══{history_text if history_text else ' (none)'}

═══ USER QUERY ═══
{message}

═══ MATCHING RULES (follow strictly) ═══
R1. ROLE FIRST — Work History job title is the #1 signal. "Java developer" → only candidates whose titles include Java/Software/Backend. Skills alone are insufficient if role doesn't match.
R2. HARD FILTERS — If LOCATION, EXPERIENCE RANGE, or EXCLUSIONS are specified, violations = immediate removal. No exceptions.
R3. OR LOGIC — If user says "X or Y" both qualify equally. Rank the best examples of each.
R4. SKILL ALIASES — Treat as equivalent:
    React = ReactJS = React.js | Node = NodeJS = Node.js | Python ≈ Django/Flask/FastAPI
    Java ≈ Spring Boot/Hibernate | C# = .NET = DotNet | AWS = Amazon Web Services
    ML = Machine Learning = AI | DevOps ≈ CI/CD + Docker + Kubernetes | SQL ≈ PostgreSQL/MySQL
    Power BI ≈ Tableau ≈ Looker | Salesforce ≈ CRM | SAP ≈ ERP
    HR = Human Resources = Talent Acquisition = Recruitment | Finance ≈ Accounting ≈ CA/CPA
R5. SENIORITY — "Senior/Lead/Principal" → 7+ yrs preferred. "Junior/Entry/Fresher" → 0-2 yrs. "Mid" → 3-6 yrs.
R6. COMPOSITE QUERIES — "Python dev with 5+ years in Dubai who speaks Arabic" = ALL conditions must be met simultaneously.
R7. NEVER INVENT — Only use data shown. If a field is N/A, don't assume it.
R8. QUALITY > QUANTITY — 3 perfect matches beat 10 wrong ones. If pool is thin, say so honestly.
R9. CERTIFICATIONS & LANGUAGES — If user mentions specific certs (PMP, CPA, CISA, AWS-SAA) or languages (Arabic, Hindi, French), these are strong signals — check the candidate's cert/langs fields.
R10. NOTICE PERIOD — "Immediate/ASAP" → prioritize candidates with "Immediate" or ≤15 days notice.

═══ OUTPUT FORMAT ═══
For EACH matched candidate (up to {num_candidates}):

**#N. [Full Name]** | [Score]% | [Category] | [X] yrs exp | [Location]
- **Why match:** 2-3 sentences citing SPECIFIC evidence (title, company, skill, worked on X)
- **Key Skills:** highlight the ones matching the query (bold them)
- **Work:** last 2 roles: "Title @ Company (duration)"
- **Fit:** ⭐⭐⭐⭐⭐ Excellent / ⭐⭐⭐⭐ Strong / ⭐⭐⭐ Good / ⭐⭐ Marginal
- **Contact:** email | phone{f"{chr(10)}- **Notice Period:** [value if available]" if any(w in query_lower for w in ['immediate', 'urgent', 'asap', 'notice', 'joining']) else ""}

---
**📊 Results Summary**
- Query understood as: [restate in your own words what you searched for]
- Pool: {relevant_count} pre-filtered → X qualified shown
- Quality signal: [Excellent/Strong/Moderate/Weak pool for this requirement]
- **Top 3 recommendations for immediate interview:** [names]
{f'- **⚠️ Gap:** [honest note if pool is thin or key criteria had few matches]' if relevant_count < num_candidates * 2 else ''}"""

            # Search: no thinking budget — pre-filter already ranked; Gemini just validates and formats
            result = await self._agenerate(prompt, temperature=0.1, max_tokens=4500, thinking_budget=0)
            text_response = result or f"I'm here to help! We have **{total} candidates** in the database. What would you like to know?"
        
        if return_candidates:
            # ── Parse which candidates Gemini ACTUALLY mentioned in its response ──
            # Gemini re-ranks and picks its own top-N from the pool, so we must
            # build candidates_lookup from the NAMES in the response text, not
            # from the original pool order. This prevents card/text mismatch.
            import re as _re
            mentioned_pattern = _re.compile(
                r'\*{0,2}#(\d+)\.\s*(.+?)(?:\s*\*{0,2}\s*\||\s*\*{0,2}\s*\n|\s*\*{2,})',
                _re.MULTILINE
            )
            mentioned_entries = mentioned_pattern.findall(text_response or '')
            
            candidates_lookup = []
            used_ids = set()
            
            if mentioned_entries:
                # Build lookup in the order Gemini listed them
                for rank_str, raw_name in mentioned_entries:
                    name_clean = raw_name.strip().strip('*').strip().lower()
                    # Find best match in pool by name
                    best_match = None
                    best_score = 0
                    for c in _selected_candidates:
                        if c.get('id', '') in used_ids:
                            continue
                        cname = (c.get('name', '') or '').lower().strip()
                        # Exact match
                        if cname == name_clean:
                            best_match = c
                            best_score = 100
                            break
                        # Starts-with or contains
                        cname_base = cname.split('–')[0].split('-')[0].strip()
                        nclean_base = name_clean.split('–')[0].split('-')[0].strip()
                        if cname_base == nclean_base or cname.startswith(nclean_base) or nclean_base.startswith(cname_base):
                            if best_score < 90:
                                best_match = c
                                best_score = 90
                        else:
                            # Word overlap matching
                            cn_words = set(cname_base.split())
                            nn_words = set(nclean_base.split())
                            overlap = len(cn_words & nn_words)
                            if overlap >= 2 or (overlap >= 1 and min(len(cn_words), len(nn_words)) <= 2):
                                score = overlap * 30
                                if score > best_score:
                                    best_match = c
                                    best_score = score
                    
                    if best_match:
                        used_ids.add(best_match.get('id', ''))
                        candidates_lookup.append({
                            'index': int(rank_str),
                            'id': best_match.get('id', ''),
                            'name': best_match.get('name', ''),
                            'matchScore': best_match.get('matchScore', best_match.get('match_score', 0)),
                            'location': best_match.get('location', ''),
                            'jobCategory': best_match.get('jobCategory', best_match.get('job_category', '')),
                            'experience': best_match.get('experience', 0),
                            'skills': best_match.get('skills', [])[:10],
                            'email': best_match.get('email', ''),
                            'phone': best_match.get('phone', ''),
                            'status': best_match.get('status', 'New'),
                            'hasResume': best_match.get('hasResume', False),
                            'summary': best_match.get('summary', ''),
                            'workHistory': best_match.get('workHistory') or best_match.get('work_history') or [],
                            'education': best_match.get('education') or [],
                            'jobSubcategory': best_match.get('jobSubcategory') or best_match.get('job_subcategory', ''),
                            'appliedDate': best_match.get('appliedDate') or best_match.get('applied_date', ''),
                        })
            
            # Fallback: if parsing found nothing, send top-N from pool
            if not candidates_lookup:
                for i, c in enumerate(_selected_candidates[:num_candidates]):
                    cid = c.get('id', '')
                    if cid in used_ids:
                        continue  # Skip duplicates in fallback too
                    used_ids.add(cid)
                    candidates_lookup.append({
                        'index': i + 1,
                        'id': c.get('id', ''),
                        'name': c.get('name', ''),
                        'matchScore': c.get('matchScore', c.get('match_score', 0)),
                        'location': c.get('location', ''),
                        'jobCategory': c.get('jobCategory', c.get('job_category', '')),
                        'experience': c.get('experience', 0),
                        'skills': c.get('skills', [])[:10],
                        'email': c.get('email', ''),
                        'phone': c.get('phone', ''),
                        'status': c.get('status', 'New'),
                        'hasResume': c.get('hasResume', False),
                        'summary': c.get('summary', ''),
                        'workHistory': c.get('workHistory') or c.get('work_history') or [],
                        'education': c.get('education') or [],
                        'jobSubcategory': c.get('jobSubcategory') or c.get('job_subcategory', ''),
                        'appliedDate': c.get('appliedDate') or c.get('applied_date', ''),
                    })
            
            result_dict = {
                'response': text_response,
                'candidates_lookup': candidates_lookup
            }
            # Cache search results for fast repeat queries
            if query_type == 'search':
                self._set_search_cache(message, num_candidates, total, result_dict)
            return result_dict
        
        return text_response

    # ==================================================================
    # INTERVIEW QUESTIONS
    # ==================================================================

    async def generate_interview_questions(
        self, candidate_data: Dict, job_description: Optional[str] = None, num_questions: int = 8
    ) -> List[Dict]:
        """Generate tailored interview questions."""
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        name = candidate_data.get('name', 'the candidate')

        job_ctx = f"\nJOB:\n{job_description[:1000]}" if job_description else ""

        prompt = f"""Generate {num_questions} interview questions for {name}.
Skills: {', '.join(skills[:15])}, Experience: {experience} years{job_ctx}

Return JSON:
{{"questions": [{{"question": "...", "type": "technical|behavioral|situational", "difficulty": "easy|medium|hard", "skill_tested": "...", "what_to_look_for": "..."}}]}}"""

        result = await self._agenerate_json(prompt, temperature=0.3, max_tokens=2048)
        if result and 'questions' in result:
            return result['questions'][:num_questions]
        return [{"question": f"Tell me about your experience with {skills[0] if skills else 'your field'}.", "type": "technical", "difficulty": "medium", "skill_tested": skills[0] if skills else "General", "what_to_look_for": "Depth of knowledge"}]

    # ==================================================================
    # JOB DESCRIPTION PARSING
    # ==================================================================

    async def parse_job_description(self, text: str) -> Dict:
        """Parse a job description into structured format."""
        prompt = f"""Parse this job description. Return ONLY valid JSON.

JOB DESCRIPTION:
{text[:4000]}

Return JSON:
{{
    "title": "Job Title",
    "department": "Department",
    "location": "Location",
    "employment_type": "Full-time/Part-time/Contract",
    "experience_required": "X years",
    "required_skills": ["skill1", "skill2"],
    "preferred_skills": ["skill1"],
    "education_required": "Minimum education",
    "responsibilities": ["resp1", "resp2"],
    "benefits": ["benefit1"],
    "salary_range": "Range if mentioned",
    "key_requirements": ["req1", "req2"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.05, max_tokens=2048)
        return result or {'title': 'Position', 'required_skills': [], 'responsibilities': []}

    # ==================================================================
    # EMAIL TEMPLATE GENERATION
    # ==================================================================

    async def generate_email_template(self, template_type: str, candidate_data: Optional[Dict] = None, job_title: Optional[str] = None) -> Dict:
        """Generate professional email templates."""
        context = ""
        if candidate_data:
            context = f"Candidate: {candidate_data.get('name', 'Candidate')}, Skills: {', '.join(candidate_data.get('skills', [])[:5])}"
        if job_title:
            context += f", Position: {job_title}"

        prompt = f"""Generate a professional recruitment email template.
Type: {template_type}
{f'Context: {context}' if context else ''}

Return JSON:
{{"subject": "Subject line", "body": "Full email body", "variables": ["{{name}}", "{{position}}"], "tips": "Usage tips"}}"""

        result = await self._agenerate_json(prompt, temperature=0.4, max_tokens=2048)
        return result or {'subject': f'Re: {template_type}', 'body': 'Template unavailable', 'variables': [], 'tips': ''}

    # ==================================================================
    # STATUS & METRICS
    # ==================================================================

    def get_status(self) -> Dict:
        """Get Gemini service status."""
        avg_time = self._total_time / self._request_count if self._request_count > 0 else 0
        return {
            'available': self.available,
            'model': self.model_name,
            'api_key_set': bool(self.api_key),
            'requests_processed': self._request_count,
            'average_response_time': round(avg_time, 2),
            'error_count': self._error_count,
            'cache_size': len(self._cache),
        }

    def clear_cache(self):
        """Clear response cache."""
        self._cache.clear()
        logger.info("🗑️ Gemini cache cleared")


# ============================================================================
# SINGLETON
# ============================================================================

_gemini_service: Optional[GeminiService] = None
_gemini_lock = threading.Lock()


def get_gemini_service() -> Optional[GeminiService]:
    """Get or create Gemini service singleton. Thread-safe via double-checked locking."""
    global _gemini_service
    if _gemini_service is None:
        with _gemini_lock:
            if _gemini_service is None:
                import os
                api_key = os.getenv("GEMINI_API_KEY", "").strip()
                model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                if api_key:
                    _gemini_service = GeminiService(api_key=api_key, model_name=model)
                else:
                    logger.info("💡 GEMINI_API_KEY not set — Gemini service not initialized")
                    return None
    return _gemini_service

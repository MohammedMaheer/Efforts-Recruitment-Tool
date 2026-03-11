"""
Google Gemini AI Service
========================
Primary AI engine for DEPLOYMENT (Cloud Run / GCP).
Uses Gemini 2.0 Flash for fast, cost-effective inference (~$0.10/1M input tokens).

Tier priority in production:
  1. Gemini (primary — fast, cheap, high quality)
  2. Ollama  (fallback — local LLM)
  3. Keyword (emergency — zero API cost)

For local development, Ollama remains primary (zero cost, full privacy).
"""

import json
import logging
import re
import time
import asyncio
import hashlib
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

    # Extract largest {...} blob
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        candidate = m.group()
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

        # Response cache
        self._cache: Dict[str, Any] = {}
        self._cache_max_size = 500
        self._cache_ttl = 3600  # 1 hour

        # Daily budget tracking — prevents runaway costs
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
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry['time'] < self._cache_ttl:
                return entry['data']
            del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any):
        if len(self._cache) >= self._cache_max_size:
            oldest = sorted(self._cache, key=lambda k: self._cache[k]['time'])[:100]
            for k in oldest:
                del self._cache[k]
        self._cache[key] = {'data': data, 'time': time.time()}

    def _generate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024, thinking_budget: int = 0) -> str:
        """Synchronous text generation via Gemini.
        
        thinking_budget: Controls Gemini 2.5 Flash's internal reasoning tokens.
          0 = disabled (cheap — use for JSON extraction / structured tasks)
          >0 = enabled with token cap (use for open-ended chat / analysis)
        Thinking tokens cost $3.50/1M vs $0.60/1M for output — disable when not needed.
        """
        if not self.available or not self._client:
            return ""
        # ── Daily budget check ──
        import datetime as _dt
        today = _dt.date.today().isoformat()
        if self._daily_call_date != today:
            self._daily_call_date = today
            self._daily_call_count = 0
        if self._daily_call_count >= self._daily_call_limit:
            logger.warning(f"⚠️ Gemini daily limit reached ({self._daily_call_limit} calls). Skipping API call.")
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

    async def _agenerate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 1024) -> Optional[Dict]:
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

        # Gemini 2.5 Flash supports 1M tokens — send up to 10K chars for thorough extraction
        prompt = f"""You are an expert resume/CV parser for a recruitment agency. Extract ALL information with maximum accuracy. Return ONLY valid JSON — no commentary.

RESUME TEXT:
{text[:10000]}

EXTRACTION RULES:
1. Extract the EXACT name, email, phone, location as written — do not guess or infer.
2. For experience_years: calculate from the earliest work start date to present. If stated explicitly (e.g., "10+ years"), use that number.
3. For skills: extract ALL technical skills, tools, programming languages, frameworks, methodologies, soft skills, and domain expertise mentioned anywhere in the resume. Include certifications-related skills too. Do NOT cap the list.
4. For work_history: extract ALL positions with full details. Include description with key responsibilities and achievements.
5. For nationality/visa: only if explicitly mentioned (e.g., "Indian national", "UAE residence visa", "US citizen").
6. For notice_period: look for phrases like "immediate joiner", "30 days notice", "available from [date]", "currently serving notice".
7. For salary: look for "current salary", "expected salary", "CTC", "package" mentions.
8. For source/portal: if the resume mentions where it was uploaded or forwarded from (Indeed, LinkedIn, Bayt, Naukri, GulfTalent etc.).

Return JSON:
{{
    "name": "Full name exactly as written",
    "email": "email address",
    "phone": "phone number with country code if present",
    "location": "City, Country (as specific as possible)",
    "nationality": "Nationality if explicitly stated, else empty",
    "linkedin": "LinkedIn URL or empty",
    "summary": "3-4 sentence professional summary capturing their expertise level and domain",
    "skills": ["ALL skills, tools, languages, frameworks, methodologies, domain expertise — be comprehensive"],
    "experience_years": 0,
    "work_history": [{{"title": "Job Title", "company": "Company Name", "period": "Start - End dates", "duration": "X years Y months", "description": "Key responsibilities and achievements in 2-3 sentences"}}],
    "education": [{{"degree": "Degree", "field": "Field of Study", "institution": "Institution Name", "year": "Graduation Year"}}],
    "certifications": ["certification names with issuing body if mentioned"],
    "languages": ["languages with proficiency level if mentioned"],
    "notice_period": "Notice period or availability if mentioned, else empty",
    "current_salary": "Current salary/CTC if mentioned, else empty",
    "expected_salary": "Expected salary if mentioned, else empty",
    "source_portal": "Job portal source if identifiable, else empty",
    "job_applied_for": "Specific job title they applied for if mentioned, else empty",
    "job_title_applied": "Their most recent/current job title"
}}

CRITICAL: Only extract data that is EXPLICITLY present in the resume. Never fabricate or hallucinate any information."""

        result = await self._agenerate_json(prompt, temperature=0.05)

        if result:
            # ── Sanitize fields: strip CID artifacts and garbage from AI output ──
            _cid_re = re.compile(r'\(cid:\d+\)')
            for field in ('phone', 'name', 'email', 'location', 'linkedin', 'summary'):
                val = result.get(field, '')
                if val and isinstance(val, str) and 'cid:' in val:
                    cleaned = _cid_re.sub('', val).strip()
                    result[field] = cleaned
            
            # ── Validate phone: must contain real digits, not garbage ──
            phone = result.get('phone', '')
            if phone:
                digits = re.sub(r'\D', '', phone)
                if len(digits) < 7 or len(digits) > 15 or 'cid' in phone.lower():
                    logger.warning(f"🚫 Rejected garbage phone: {phone[:50]}")
                    result['phone'] = ''
            
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
        # Increased budget from 2.5K to 5K for richer extraction
        body_section = body[:3500]
        resume_section = ""
        if resume_text and len(resume_text.strip()) > 50:
            resume_section = f"\n\nRESUME TEXT (from attached file):\n{resume_text[:5000]}"

        taxonomy_text = get_taxonomy_prompt_text()

        prompt = f"""You are an expert recruitment data extraction AI. Parse this job application email and extract ALL candidate information with maximum accuracy. Return ONLY valid JSON.

SUBJECT: {subject}
SENDER: {sender}
SOURCE: {source}

EMAIL BODY:
{body_section}
{resume_section}

JOB TAXONOMY (use these EXACT category/subcategory names):
{taxonomy_text}

Return ONLY valid JSON with this EXACT structure:
{{
    "name": "Full name of the candidate",
    "email": "Candidate email (NOT the portal noreply address)",
    "phone": "Phone with country code or empty",
    "location": "City, Country or empty",
    "skills": ["Extract ALL technical and professional skills mentioned — be thorough"],
    "experience_years": 0,
    "summary": "2-4 sentence professional summary based on actual content",
    "linkedin": "LinkedIn URL or empty",
    "job_applied_for": "Position title the candidate applied for",
    "source": "{source}",
    "nationality": "Nationality if mentioned or empty",
    "notice_period": "Notice period if mentioned or empty",
    "current_salary": "Current salary if mentioned or empty",
    "expected_salary": "Expected salary if mentioned or empty",
    "work_history": [
        {{"title": "Job title", "company": "Company name", "period": "Date range", "description": "Key responsibilities/achievements"}}
    ],
    "education": [
        {{"degree": "Degree type", "field": "Field of study", "institution": "University/College", "year": "Graduation year"}}
    ],
    "certifications": ["List any certifications mentioned"],
    "languages": ["Languages spoken if mentioned"],
    "job_category": "Best matching category from taxonomy",
    "job_subcategory": "Specific role subcategory from taxonomy",
    "quality_score": 65,
    "is_candidate_email": true
}}

CRITICAL RULES:
- Set is_candidate_email to false if this is NOT a job application (e.g., newsletter, invoice, internal email)
- Extract the candidate's ACTUAL email, not system/noreply addresses
- For skills: include ALL technical skills, tools, frameworks, methodologies, and relevant soft skills
- For work_history: extract EVERY position with title, company, dates, and description
- For education: extract ALL degrees with institution name and field
- NEVER fabricate data — use empty strings/arrays for missing fields
- For experience_years: calculate from work history or use explicitly stated number
- For quality_score: Rate 85-100 Exceptional (10+ yrs, strong skills, leadership), 70-84 Strong (5+ yrs, good skills), 55-69 Moderate (2-5 yrs, some skills), 40-54 Developing (entry-level), below 40 Weak. Be precise — do NOT default to 50 or 65."""

        try:
            result = await self._agenerate_json(prompt, temperature=0.05)
        except Exception as gen_err:
            logger.warning(f"Gemini JSON generation error: {gen_err}")
            return None

        if result and isinstance(result, dict):
            if not result.get('is_candidate_email', True):
                return None
            result['source'] = source
            # Validate category
            if not result.get('job_subcategory'):
                title = result.get('job_applied_for', '')
                if title:
                    cat, sub = classify_job_title(title)
                    result['job_category'] = cat
                    result['job_subcategory'] = sub
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

Be precise and differentiated. Do NOT default to 50 or 65. Assess the actual resume quality carefully.
If the resume shows 10+ skills, clear experience, and education, score 75+.
If sparse/generic, score 35-50."""

        prompt = f"""You are an expert recruitment AI analyzing a candidate's resume/profile.
Extract ALL structured information and provide an honest quality assessment.
{job_instruction}

RESUME TEXT:
{text[:4000]}

Return ONLY valid JSON with these exact fields:
{{
    "name": "Full Name from resume (if visible)",
    "phone": "Phone number with country code if found",
    "email": "Email address if found",
    "location": "City, State/Country — extract from address, contact info, or any location mention",
    "skills": ["Extract ALL technical and professional skills mentioned — be thorough, list 10+ if present"],
    "experience": 5,
    "education": ["Highest degree — e.g. B.Tech in Computer Science, MBA, etc."],
    "job_category": "MUST be exactly one of: Software Engineering, Data & Analytics, IT & Systems, Engineering, HR & Admin, Finance & Accounting, Sales, Operations, Consulting, Healthcare, Design & Creative, QA & Testing, Marketing, Customer Service, Insurance & Safety, Retail & Hospitality, Business Analyst, Education, Legal, General",
    "job_subcategory": "Specific role title — e.g. Full Stack Developer, MEP Engineer, Data Scientist",
    "quality_score": 72,
    "summary": "2-3 sentence professional summary highlighting key strengths and experience level",
    "certifications": ["Any certifications mentioned"],
    "languages": ["Languages spoken if mentioned"],
    "linkedin": "LinkedIn URL if found",
    "work_history": ["Recent company/role if mentioned"]
}}

IMPORTANT for quality_score:
- Base it on: depth of experience, breadth of skills, education quality, certifications, career progression
- A resume with 10+ skills, 5+ years exp, and a degree should score 70-80
- A resume with 3-4 skills and 1-2 years should score 45-55
- Score MUST reflect actual resume content — never default to 50 or 65"""

        result = await self._agenerate_json(prompt, temperature=0.1)

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
                score = int(nums[0]) if nums else 40
            try:
                result['match_score'] = max(10, min(100, int(float(score))))
            except (TypeError, ValueError):
                result['match_score'] = 40
            result['quality_score'] = result['match_score']
            result.setdefault('job_category', 'General')
            result.setdefault('skills', [])
            result.setdefault('experience', 0)
            result.setdefault('summary', '')
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

        work_text = ""
        if work_history:
            for w in work_history[:4]:
                if isinstance(w, dict):
                    work_text += f"\n  - {w.get('title', '')} at {w.get('company', '')} ({w.get('period', '')})"

        prompt = f"""You are a senior recruiter. Provide specific, data-driven candidate assessment. Return ONLY valid JSON.

Name: {name}
Experience: {experience} years
Skills: {', '.join(skills[:20]) if skills else 'Not listed'}
Education: {json.dumps(education[:3], default=str) if education else 'N/A'}
Work History:{work_text or ' N/A'}
Summary: {summary[:300] if summary else 'N/A'}

Return JSON:
{{
    "executive_summary": "2-3 sentence assessment",
    "pros": ["pro 1", "pro 2", "pro 3"],
    "cons": ["con 1", "con 2"],
    "ideal_roles": ["Role 1", "Role 2"],
    "interview_focus_areas": ["Topic 1", "Topic 2"],
    "hiring_recommendation": "STRONGLY_RECOMMEND|RECOMMEND|CONSIDER|PASS",
    "confidence_score": 85,
    "overall_rating": "A|B+|B|C+|C|D",
    "strengths": ["strength 1", "strength 2"],
    "weaknesses": ["weakness 1"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.15)

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
        cache_key = self._cache_key("match", f"{json.dumps(candidate_data, default=str)}:{job_description[:500]}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))

        prompt = f"""Evaluate candidate-job fit. Return ONLY valid JSON.

CANDIDATE: {name}
Skills: {', '.join(skills[:20]) if skills else 'Not specified'}
Experience: {experience} years
Summary: {candidate_data.get('summary', '')[:400]}

JOB:
{job_description[:2000]}

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

        result = await self._agenerate_json(prompt, temperature=0.15)

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

        # Detect explicit location requirement from JD
        raw_loc_terms = _extract_location_from_text(job_description)
        jd_location_terms = _expand_location_terms(raw_loc_terms)
        jd_has_location = len(jd_location_terms) > 0

        # Detect experience requirement from JD
        _exp_match_jd = EXPERIENCE_PATTERN.search(job_description)
        jd_min_experience = int(_exp_match_jd.group(1)) if _exp_match_jd else 0

        pre_scored = []
        for idx, c in enumerate(candidates):
            skills = [s.lower().strip() for s in c.get('skills', [])]
            category = (c.get('jobCategory') or c.get('job_category') or '').lower()
            subcategory = (c.get('jobSubcategory') or c.get('job_subcategory') or '').lower()
            location = (c.get('location') or '').lower()
            summary = (c.get('summary') or '').lower()

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

            # Location scoring — strong when location requirement detected
            loc_score_add = 0
            if jd_has_location:
                loc_words = set(re.sub(r'[^\w\s]', ' ', location).split())
                loc_matched = any(lt in location or lt in loc_words for lt in jd_location_terms)
                if loc_matched:
                    loc_score_add = 50  # Strong boost
                else:
                    loc_score_add = -30  # Penalty
            else:
                loc_hits = sum(1 for kw in expanded_keywords if kw in location)
                loc_score_add = loc_hits * 8

            summary_words = set(summary.split())
            summary_hits = len(summary_words & expanded_keywords)

            exp = _safe_int_experience(c.get('experience', 0))
            
            # Experience scoring — meaningful weight
            exp_score = min(exp, 20) * 1.5  # Up to 30 points from experience
            if jd_min_experience > 0:
                if exp >= jd_min_experience:
                    exp_score += 15  # Bonus for meeting minimum
                else:
                    exp_score -= 15  # Penalty for below minimum

            pre_score = skill_hits * 15 + cat_hits * 10 + loc_score_add + min(summary_hits, 5) * 3 + exp_score
            pre_scored.append((pre_score, idx, c))

        pre_scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        # Take more candidates for better Gemini recall
        deep_count = min(max(top_n * 3, 30), len(pre_scored))
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
            ck = self._cache_key("fast_match", f"{c.get('name','')}:{c.get('email','')}:{job_description[:200]}")
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
                        ck = self._cache_key("fast_match", f"{c.get('name','')}:{c.get('email','')}:{job_description[:200]}")
                        self._set_cache(ck, match_data)
                        batch_results_list.append({'candidate': c, 'match': match_data, 'score': match_data.get('match_score', 0)})
                    else:
                        ps = next((p for p, _, cc in pre_scored if cc is c), 0)
                        batch_results_list.append({
                            'candidate': c,
                            'match': {'match_score': min(int(ps * 2), 100), 'strengths': ['Pre-filter matched'], 'gaps': ['Deep analysis pending']},
                            'score': min(int(ps * 2), 100),
                        })
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
            skills_str = ', '.join(c.get('skills', [])[:12]) or 'Not specified'
            candidates_text += f"\nCANDIDATE {i}: {c.get('name', 'Unknown')}\n  Skills: {skills_str}\n  Experience: {c.get('experience', 0)} years\n  Summary: {c.get('summary', '')[:200]}\n"

        n = len(batch)
        prompt = f"""Score each candidate against the job. Return ONLY valid JSON with a "candidates" array of exactly {n} objects.

{candidates_text}

JOB:
{job_description[:1500]}

Return: {{"candidates": [{{"match_score": 75, "matched_skills": ["Python"], "missing_skills": ["Go"], "strengths": ["Strong backend"], "gaps": ["No cloud"], "recommendation": "Good fit"}}]}}"""

        result = await self._agenerate_json(prompt, temperature=0.1)
        if not result:
            raise ValueError("Empty Gemini batch response")

        batch_results = result.get('candidates', [])
        if isinstance(result, list):
            batch_results = result

        normalized = []
        for item in batch_results:
            if not isinstance(item, dict):
                continue
            score = item.get('match_score', 50)
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 50
            item['match_score'] = max(0, min(100, int(score)))
            normalized.append(item)

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

        result = await self._agenerate_json(prompt, temperature=0.2)
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

        # Analytics / statistics queries
        analytics_signals = [
            'how many', 'count', 'total', 'statistics', 'stats', 'average', 'breakdown',
            'distribution', 'percentage', 'ratio', 'trend', 'report', 'summary of',
            'overview', 'dashboard', 'analyze the database', 'analyze our', 'number of',
            'pie chart', 'bar chart', 'graph', 'metric', 'kpi',
        ]
        if any(sig in msg for sig in analytics_signals):
            return 'analytics'

        # Comparison queries
        if any(w in msg for w in ['compare', 'comparison', 'versus', 'vs', 'better between',
                                   'side by side', 'which one', 'who is better', 'rank these',
                                   'difference between']):
            return 'comparison'

        # ── Search signal detection (expanded) ──
        search_overrides = [
            'find', 'show', 'list', 'get', 'candidates', 'shortlist', 'search',
            'filter', 'locate', 'identify', 'recruit', 'who', 'look for', 'fetch',
            'cvs', 'profiles', 'resumes', 'applicants', 'people',
            'experience', 'years', 'location', 'based in', 'skills', 'developer',
            'engineer', 'manager', 'designer', 'analyst', 'consultant', 'accountant',
            'administrator', 'coordinator', 'specialist', 'architect', 'director',
            'nurse', 'doctor', 'teacher', 'driver', 'technician', 'executive',
            'available', 'immediate joiner', 'with skills', 'proficient in',
            'who knows', 'who has', 'who can', 'working in', 'worked in',
            'having', 'holding', 'certified in', 'speaks', 'speaking',
            'nationality', 'passport', 'visa', 'notice period',
            # Extended role titles for better classification
            'sales', 'marketing', 'finance', 'accounting', 'hr',
            'recruiter', 'programmer', 'tester', 'qa', 'devops', 'data',
            'product', 'project', 'operations', 'logistics', 'procurement',
            'auditor', 'receptionist', 'secretary', 'clerk', 'pharmacist',
            'chef', 'electrician', 'plumber', 'mechanic', 'welder',
            'intern', 'trainee', 'fresher', 'graduate',
            # Industry terms that imply candidate search
            'it company', 'it service', 'banking sector', 'healthcare',
            'manufacturing', 'retail', 'ecommerce', 'startup',
        ]
        has_search_signal = any(w in msg for w in search_overrides)
        
        # Build word set once for all signal checks
        msg_word_set = set(re.sub(r'[^\w\s]', ' ', msg).split())
        
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
                for part in re.split(r'[,;&]+', neg_raw):
                    part = part.strip()
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
            
            # Dynamic pool size — optimized for Gemini 2.5 Flash throughput
            # Compact profiles: 15 candidates keeps prompt well within 120s timeout
            has_specific_keywords = len(keywords) >= 2
            has_many_keywords = len(keywords) >= 4
            
            if has_many_keywords:
                MAX_CANDIDATES_TO_GEMINI = 20  # Complex query — enough for thorough ranking
            elif has_specific_keywords:
                MAX_CANDIDATES_TO_GEMINI = 25  # Moderate query
            else:
                MAX_CANDIDATES_TO_GEMINI = 30  # Broad/simple query
            
            # Ensure we request at least enough for the user's num_candidates
            MAX_CANDIDATES_TO_GEMINI = max(MAX_CANDIDATES_TO_GEMINI, min(num_candidates + 5, 35))
            
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
            
            # Build context — lean format for fast Gemini processing
            # Focus on essential info: name, skills, work titles, location, experience
            candidates_context = f"\n\nCANDIDATES ({relevant_count} pre-filtered from {total_scanned}):\n"
            for i, (rel_score, _idx, c) in enumerate(selected[:MAX_CANDIDATES_TO_GEMINI]):
                skills_raw = c.get('skills', [])
                skills_str = ', '.join(skills_raw[:25]) if isinstance(skills_raw, list) else str(skills_raw or '')
                work = c.get('workHistory', c.get('work_history', []))
                if isinstance(work, list):
                    work_entries = []
                    for w in work[:4]:  # Show up to 4 positions for better context
                        if isinstance(w, dict):
                            entry = f"{w.get('title', 'N/A')} @ {w.get('company', 'N/A')}"
                            dur = w.get('duration', w.get('period', ''))
                            if dur:
                                entry += f" ({dur})"
                            desc = w.get('description', '')
                            if desc and len(str(desc)) > 10:
                                entry += f" — {str(desc)[:120]}"
                            work_entries.append(entry)
                    work_str = '; '.join(work_entries) or 'N/A'
                else:
                    work_str = str(work)[:200] if work else 'N/A'
                edu = c.get('education', [])
                edu_parts = []
                if isinstance(edu, list):
                    for ed in edu[:3]:
                        if isinstance(ed, dict):
                            edu_parts.append(' - '.join(p for p in [ed.get('degree', ''), ed.get('field', ''), ed.get('institution', ''), ed.get('year', '')] if p))
                edu_str = '; '.join(edu_parts) if edu_parts else 'N/A'
                
                candidates_context += (
                    f"[{i+1}] {c.get('name', 'Unknown')} | {c.get('matchScore', 0)}% | "
                    f"{c.get('jobCategory', c.get('job_category', 'General'))} | "
                    f"Exp: {c.get('experience', 0)}yrs | {c.get('location', 'N/A')} | "
                    f"Status: {c.get('status', 'New')}\n"
                    f"   Skills: {skills_str}\n"
                    f"   Work: {work_str}\n"
                    f"   Edu: {edu_str}\n"
                    f"   Contact: {c.get('email', 'N/A')} | {c.get('phone', 'N/A')}\n"
                )
                # Add summary for richer context
                _summary = c.get('summary', '')
                if _summary and len(str(_summary)) > 10:
                    candidates_context += f"   Summary: {str(_summary)[:200]}\n"
                # Append enriched fields only when they have meaningful values
                _extra_lines = []
                _nat = c.get('nationality', '')
                _notice = c.get('notice_period', '')
                _salary = c.get('current_salary', '') or c.get('expected_salary', '')
                _portal = c.get('source_portal', '')
                _job_app = c.get('job_applied_for', '')
                _certs = c.get('certifications', [])
                _langs = c.get('languages', [])
                if _nat: _extra_lines.append(f"Nationality: {_nat}")
                if _notice: _extra_lines.append(f"Notice: {_notice}")
                if _salary: _extra_lines.append(f"Salary: {c.get('current_salary', '')} → {c.get('expected_salary', '')}")
                if _portal and _portal != 'Direct': _extra_lines.append(f"Source: {_portal}")
                if _job_app: _extra_lines.append(f"Applied for: {_job_app}")
                if isinstance(_certs, list) and _certs: _extra_lines.append(f"Certs: {', '.join(str(x) for x in _certs[:5])}")
                if isinstance(_langs, list) and _langs: _extra_lines.append(f"Languages: {', '.join(str(x) for x in _langs[:5])}")
                if _extra_lines:
                    candidates_context += f"   {' | '.join(_extra_lines)}\n"

        # Build conversation context
        history_text = ""
        if conversation_history:
            # Keep last 8 messages, prioritize recent full context
            for msg in conversation_history[-8:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')[:400]
                history_text += f"\n{role}: {content}"

        cat_list = ', '.join([f"{k}: {v}" for k, v in list(categories.items())[:12]]) if categories else 'N/A'

        # Build dynamic constraint sections
        constraints = []
        if detected_roles:
            constraints.append(f"TARGET ROLE(S) (HIGH PRIORITY): The user is looking for: {', '.join(detected_roles)}. Candidates whose job titles or categories match these roles MUST be ranked highest. Candidates with completely different roles should be ranked very low or excluded.")
        if or_alternatives:
            constraints.append(f"OR-ALTERNATIVE ROLES: The user accepts ANY of these: {', '.join(or_alternatives)}. A candidate matching ANY one of these alternatives is a valid match.")
        if detected_industries:
            constraints.append(f"INDUSTRY/DOMAIN FILTER: The user wants candidates from the {', '.join(detected_industries)} industry/sector. Prioritize candidates whose work history shows experience in these domains. Candidates from completely unrelated industries should be ranked lower.")
        if has_location_requirement:
            loc_str = ', '.join(required_location_terms)
            constraints.append(f"LOCATION FILTER (MANDATORY): Candidates in/near {loc_str} MUST be ranked first. Only include non-local candidates if fewer than {num_candidates} match locally. Flag non-local candidates clearly.")
        if required_max_experience < 999:
            constraints.append(f"EXPERIENCE RANGE FILTER (STRICT): Only {required_min_experience}-{required_max_experience} years. EXCLUDE any candidate with more than {required_max_experience} years of experience — this is a hard requirement, not a preference.")
        elif required_min_experience > 0:
            constraints.append(f"EXPERIENCE FILTER: Minimum {required_min_experience}+ years. Flag candidates below this threshold.")
        if negative_terms:
            constraints.append(f"EXCLUSION FILTER (STRICT): EXCLUDE candidates matching: {', '.join(negative_terms)}. This is a hard filter — do NOT include any candidate whose skills, category, role, or background matches these terms.")
        if required_seniority:
            constraints.append(f"SENIORITY FILTER: Target seniority level is '{required_seniority}'. Prioritize candidates whose experience level matches this seniority.")
        
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
            prompt = f"""You are a world-class Senior Recruitment Strategist and HR Expert for Efforts Solutions, a recruitment agency. You have 20+ years of experience across tech, finance, healthcare, engineering, and executive hiring.

You have deep expertise in:
- Talent acquisition strategy & sourcing methodologies
- Interview design, behavioral & competency-based questioning
- Compensation benchmarking & offer negotiation
- Employer branding & candidate experience
- Diversity, equity & inclusion in hiring
- Applicant tracking systems & recruitment technology
- Labor market trends across GCC, India, US, UK, Europe
- Industry-specific hiring (IT, finance, engineering, healthcare, sales, operations)
- Remote/hybrid workforce management
- Onboarding best practices

DATABASE CONTEXT: You have access to a database of {total} candidates across these categories: {cat_list}

CONVERSATION HISTORY:{history_text}

USER QUESTION:
{message}

─── RESPONSE GUIDELINES ───
1. Provide actionable, expert-level advice grounded in real recruitment best practices
2. Use specific examples, frameworks, or methodologies where relevant
3. Reference industry standards (e.g., SHRM, LinkedIn Talent Insights, Glassdoor data)
4. Structure your response with clear headers, bullet points, and numbered steps
5. If the question relates to roles in the database, reference the candidate pool size
6. Include pro tips, common pitfalls to avoid, and red/green flags
7. Be concise but thorough — aim for comprehensive yet scannable responses
8. Use bold text for key terms and headers for structure
9. When discussing salaries/compensation, acknowledge regional variations (GCC vs India vs US/UK)
10. End with a specific actionable recommendation or next step

Write in a professional, confident, and helpful tone. Format with markdown."""

            result = await self._agenerate(prompt, temperature=0.3, max_tokens=4000, thinking_budget=4096)
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

            prompt = f"""You are an expert Recruitment Analytics Advisor for Efforts Solutions. Analyze the data and provide clear, insightful answers with specific numbers.

{stats_detail}

CONVERSATION HISTORY:{history_text}

USER QUESTION:
{message}

─── RESPONSE GUIDELINES ───
1. Lead with the specific numbers and data the user asked about
2. Present data in clear tables or bullet lists with bold labels
3. Provide context and insights — don't just state numbers, explain what they mean
4. Highlight trends, strengths, and gaps in the talent pool
5. Compare against industry benchmarks where possible
6. Suggest actionable steps based on the data (e.g., "You have a gap in senior DevOps — consider posting on specialized job boards")
7. Use percentages and ratios for clearer understanding
8. If the user asks about something not in the data, say so clearly and suggest alternatives
9. Format with markdown headers, bold labels, and organized structure
10. Keep it data-driven and precise — recruiters need facts, not fluff"""

            result = await self._agenerate(prompt, temperature=0.15, max_tokens=4000, thinking_budget=4096)
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

            result = await self._agenerate(prompt, temperature=0.2, max_tokens=6000, thinking_budget=4096)
            text_response = result or "Could you provide more details about what you'd like me to do next?"

        else:
            # ══════════════════════════════════════════════════════════
            # CANDIDATE SEARCH — Optimized prompt for speed + quality
            # ══════════════════════════════════════════════════════════

            prompt = f"""You are an expert AI Recruitment Specialist for Efforts Solutions. Your task is to analyze the candidate pool and return the BEST matches for the user's query with surgical precision.

DATABASE: {total} candidates total | {strong} strong matches (70%+) | Categories: {cat_list}

ACTIVE FILTERS:
{constraints_text}

PRE-FILTER INTELLIGENCE:
• Detected roles: {', '.join(detected_roles) if detected_roles else 'Not specified'}
• OR alternatives: {', '.join(or_alternatives) if or_alternatives else 'None'}
• Industries: {', '.join(detected_industries) if detected_industries else 'Not specified'}
• Key phrases: {', '.join(list(query_phrases)[:8]) if query_phrases else 'None'}

{candidates_context}

CONVERSATION:{history_text}

USER QUERY: {message}

═══════════════════════════════════════
STEP 1 — QUERY DECOMPOSITION (do this internally before ranking)
═══════════════════════════════════════
Parse the user's COMPLETE intent. Users write in natural language — interpret their full meaning:
• ROLE/TITLE: What job role(s)? Handle OR alternatives ("sales or account manager" = either role is valid). Check the "Detected roles" and "OR alternatives" above.
• MUST-HAVE SKILLS: Non-negotiable technical/professional skills
• NICE-TO-HAVE SKILLS: Preferred but not mandatory
• LOCATION: Required city/country/region (if specified). Handle OR locations ("uae or india" = either is valid).
• EXPERIENCE: Exact range or minimum (if specified)  
• SENIORITY: Junior/Mid/Senior/Lead/Director (if implied or stated)
• INDUSTRY/DOMAIN: Industry vertical (e.g. "IT service company" = must have IT/software industry background). Check "Industries" above.
• EXCLUSIONS: What to explicitly exclude
• LANGUAGE/NATIONALITY: If mentioned
• AVAILABILITY: Notice period / urgency (if mentioned)

═══════════════════════════════════════
STEP 2 — STRICT MATCHING RULES (MANDATORY)
═══════════════════════════════════════
1. ROLE MATCH: If the user asks for a specific role (e.g. "account manager"), the candidate's ACTUAL job title or category MUST match. A "software developer" does NOT qualify as an "account manager". Check their Work History titles — this is the strongest signal.
2. LOCATION: If the user specifies a location, ONLY return candidates from that exact location (city-level match). If user says "X or Y", candidates from EITHER location qualify. If you cannot find enough, expand to the same country but ALWAYS flag non-local candidates with "⚠️ Not in [city]".
3. EXPERIENCE: Enforce the exact range. "5+ years" means >= 5. "1-5 years" means >= 1 AND <= 5. Do NOT bend this rule.
4. INDUSTRY: If the user mentions an industry (e.g. "IT service company", "banking sector"), prioritize candidates whose work history shows companies in that industry.
5. CORE SKILLS: The candidate MUST have the primary skill or role mentioned in the query. A Java developer does NOT qualify for a sales manager role. Do NOT return role/skill mismatches.
6. EXCLUSIONS: "exclude", "not", "no", "without", "ONLY" = absolute deal-breakers. Zero tolerance.
7. OR CONDITIONS: "X or Y" means the candidate can match EITHER X or Y — they don't need both.
8. VERIFICATION: Before including ANY candidate, mentally verify they pass ALL hard filters. If they fail even ONE, DROP them. It is BETTER to return fewer but accurate results than to pad the list with mismatches.

Skill equivalence map:
React=ReactJS=React.js | Node=NodeJS=Node.js | Vue=VueJS=Vue.js | Angular=AngularJS
Python≈Django/Flask/FastAPI | Java≈Spring Boot | C#=.NET=ASP.NET | Go=Golang
AWS=Amazon Web Services | GCP=Google Cloud | Azure=Microsoft Cloud | K8s=Kubernetes
ML=Machine Learning | AI=Artificial Intelligence | NLP=Natural Language Processing
SQL≈PostgreSQL/MySQL/Oracle | MongoDB=Mongo | NoSQL≈Redis/Cassandra/DynamoDB
DevOps≈CI/CD+Docker+Kubernetes | Agile=Scrum | RPA=UiPath/BluePrism/Automation Anywhere

Role equivalence map:
Sales Manager≈Business Development Manager≈Revenue Manager
Account Manager≈Key Account Manager≈Client Manager≈Relationship Manager
Project Manager≈Program Manager≈Delivery Manager
HR Manager≈People Manager≈Talent Manager
Marketing Manager≈Brand Manager≈Growth Manager

═══════════════════════════════════════
STEP 3 — SCORING MATRIX (for ranking qualified candidates)
═══════════════════════════════════════
Weight each dimension:
• Role/Title Match (30%): Does the candidate's ACTUAL job title match the requested role? Check work history titles. This is the STRONGEST signal.
• Core Skill Match (25%): Does the candidate have the exact skills requested? Check skills list AND work history.
• Experience Fit (20%): Do their years and seniority level match? Is their career trajectory aligned?
• Location Match (15%): Exact city > same country > same region. Heavily penalize wrong locations when location is specified. 
• Domain/Industry Fit (10%): Same industry or transferable domain experience.

═══════════════════════════════════════
OUTPUT FORMAT — Return up to {num_candidates} candidates (or fewer if not enough qualify)
═══════════════════════════════════════
IMPORTANT: If fewer than {num_candidates} candidates genuinely match, return only those that match. Do NOT pad with irrelevant candidates just to fill the count. Quality > Quantity.

**#N. Full Name** | Score: X% | Category | Exp: X yrs | Location
- **Key Skills:** list relevant skills (BOLD the ones that match the query)
- **Work History:** most recent 2-3 roles with company names and key achievements
- **Why This Candidate:** 3-4 sentences — reference SPECIFIC skills and experience from their profile that match the query. Be honest about any gaps. Mention transferable experience.
- **Risk Level:** Low/Medium/High — explain briefly (e.g., "Low — exact stack match, right seniority, same city")
- **Fit:** ⭐⭐⭐⭐⭐ Excellent / ⭐⭐⭐⭐ Strong / ⭐⭐⭐ Good / ⭐⭐ Partial
- **Contact:** email, phone
{f"- **Notice Period:** notice period if available" if any(w in query_lower for w in ['immediate', 'urgent', 'asap', 'available', 'notice']) else ""}

═══════════════════════════════════════
FOOTER (ALWAYS include)
═══════════════════════════════════════
**📊 Search Intelligence**
- Query interpretation: [what you understood from the query]
- Filters applied: [location, experience, skills, exclusions]
- Pool: {relevant_count} pre-filtered → X qualified → {num_candidates} returned
- Pool quality: [Strong/Moderate/Weak] for this role

**💡 Recommendations**
- Interview priority order (top 3 by rank)
- Key screening questions for the top candidates  
- If pool is thin: suggest criteria to relax or alternative job titles
- If pool is strong: suggest additional differentiators to narrow down"""

            result = await self._agenerate(prompt, temperature=0.15, max_tokens=6000, thinking_budget=8192)
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
                    })
            
            return {
                'response': text_response,
                'candidates_lookup': candidates_lookup
            }
        
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

        result = await self._agenerate_json(prompt, temperature=0.3)
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

        result = await self._agenerate_json(prompt, temperature=0.05)
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

        result = await self._agenerate_json(prompt, temperature=0.4)
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


def get_gemini_service() -> Optional[GeminiService]:
    """Get or create Gemini service singleton."""
    global _gemini_service
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

"""
Hierarchical Job Taxonomy System
Comprehensive category → subcategory mapping for ALL job types.
Used by AI services for accurate candidate classification.
"""
from typing import Dict, List, Optional, Tuple
import re

# =============================================================================
# MASTER JOB TAXONOMY — Category → Subcategories
# =============================================================================

JOB_TAXONOMY: Dict[str, List[str]] = {
    # ── Technology & Engineering ──
    "Software Engineer": [
        "Frontend Developer",
        "Backend Developer",
        "Full-Stack Developer",
        "Mobile Developer (iOS)",
        "Mobile Developer (Android)",
        "Mobile Developer (Cross-Platform)",
        "Embedded Systems Engineer",
        "Game Developer",
        "Desktop Application Developer",
        "API Developer",
        "Firmware Engineer",
        "Low-Level / Systems Programmer",
    ],
    "DevOps Engineer": [
        "Cloud Engineer",
        "Site Reliability Engineer (SRE)",
        "Infrastructure Engineer",
        "Platform Engineer",
        "Release Engineer",
        "Build & Deploy Engineer",
        "Kubernetes / Container Engineer",
        "CI/CD Engineer",
    ],
    "Data Scientist": [
        "Machine Learning Engineer",
        "Data Analyst",
        "Data Engineer",
        "Business Intelligence Analyst",
        "AI / Deep Learning Engineer",
        "NLP Engineer",
        "Computer Vision Engineer",
        "Quantitative Analyst",
        "Research Scientist",
        "MLOps Engineer",
    ],
    "Cybersecurity": [
        "Security Analyst",
        "Penetration Tester",
        "Security Engineer",
        "SOC Analyst",
        "Application Security Engineer",
        "Cloud Security Engineer",
        "Compliance Analyst",
        "Incident Response Analyst",
        "Threat Intelligence Analyst",
    ],
    "QA / Testing": [
        "QA Engineer",
        "Automation Test Engineer",
        "Manual Tester",
        "Performance Tester",
        "SDET (Software Dev Engineer in Test)",
        "QA Lead",
        "Mobile QA Engineer",
    ],
    "IT & Systems": [
        "System Administrator",
        "Network Engineer",
        "Database Administrator (DBA)",
        "IT Support Engineer",
        "Help Desk Technician",
        "IT Manager",
        "Solutions Architect",
        "Enterprise Architect",
        "Cloud Architect",
        "Technical Support Engineer",
    ],

    # ── Product & Design ──
    "Product Manager": [
        "Technical Product Manager",
        "Product Owner",
        "Associate Product Manager",
        "Senior Product Manager",
        "Growth Product Manager",
        "Platform Product Manager",
        "AI Product Manager",
    ],
    "Design": [
        "UI Designer",
        "UX Designer",
        "UI/UX Designer",
        "Graphic Designer",
        "Visual Designer",
        "Interaction Designer",
        "UX Researcher",
        "Motion Designer",
        "Product Designer",
        "Brand Designer",
        "Design System Designer",
    ],

    # ── Business & Management ──
    "Project Management": [
        "Project Manager",
        "Scrum Master",
        "Agile Coach",
        "Program Manager",
        "PMO Analyst",
        "Delivery Manager",
        "Technical Project Manager",
    ],
    "Business Analyst": [
        "Business Analyst",
        "Systems Analyst",
        "Requirements Analyst",
        "Process Analyst",
        "Business Process Engineer",
        "Functional Consultant",
    ],
    "Consulting": [
        "Management Consultant",
        "Strategy Consultant",
        "Technology Consultant",
        "ERP Consultant",
        "SAP Consultant",
        "Salesforce Consultant",
    ],

    # ── Marketing & Communications ──
    "Marketing": [
        "Digital Marketing Specialist",
        "Content Marketing Manager",
        "SEO Specialist",
        "SEM / PPC Specialist",
        "Social Media Manager",
        "Brand Manager",
        "Marketing Analyst",
        "Growth Marketer",
        "Email Marketing Specialist",
        "Performance Marketing Manager",
        "Marketing Coordinator",
        "Influencer Marketing Manager",
    ],
    "Content & Communications": [
        "Content Writer",
        "Copywriter",
        "Technical Writer",
        "Communications Specialist",
        "PR Specialist",
        "Journalist",
        "Editor",
        "Content Strategist",
    ],

    # ── Sales & Business Development ──
    "Sales": [
        "Account Executive",
        "Sales Development Representative (SDR)",
        "Business Development Manager",
        "Inside Sales Representative",
        "Enterprise Sales Manager",
        "Sales Engineer",
        "Key Account Manager",
        "Regional Sales Manager",
        "Sales Operations Analyst",
        "Channel Sales Manager",
    ],

    # ── Finance & Accounting ──
    "Finance": [
        "Financial Analyst",
        "Accountant",
        "Auditor",
        "Tax Analyst",
        "Treasury Analyst",
        "Investment Analyst",
        "Risk Analyst",
        "FP&A Analyst",
        "Controller",
        "CFO / Finance Director",
        "Bookkeeper",
        "Accounts Payable / Receivable",
    ],

    # ── Human Resources ──
    "HR": [
        "HR Generalist",
        "Recruiter / Talent Acquisition",
        "HR Business Partner",
        "Compensation & Benefits Specialist",
        "Learning & Development Specialist",
        "HR Operations / Admin",
        "People Analytics Specialist",
        "Employee Relations Specialist",
        "HR Manager / Director",
        "Employer Branding Specialist",
    ],

    # ── Legal & Compliance ──
    "Legal": [
        "Corporate Lawyer",
        "Legal Counsel",
        "Paralegal",
        "Compliance Officer",
        "Contract Manager",
        "IP / Patent Attorney",
        "Privacy / Data Protection Officer",
        "Regulatory Affairs Specialist",
    ],

    # ── Operations & Supply Chain ──
    "Operations": [
        "Operations Manager",
        "Supply Chain Manager",
        "Logistics Coordinator",
        "Procurement Specialist",
        "Warehouse Manager",
        "Inventory Analyst",
        "Quality Assurance Manager",
        "Vendor Manager",
        "Facilities Manager",
    ],

    # ── Healthcare & Life Sciences ──
    "Healthcare": [
        "Doctor / Physician",
        "Nurse",
        "Pharmacist",
        "Medical Lab Technologist",
        "Physiotherapist",
        "Healthcare Administrator",
        "Clinical Research Associate",
        "Biomedical Engineer",
        "Health Informatics Specialist",
        "Dentist",
    ],

    # ── Education & Training ──
    "Education": [
        "Teacher / Instructor",
        "Professor / Lecturer",
        "Curriculum Developer",
        "Academic Advisor",
        "Training Coordinator",
        "E-Learning Developer",
        "Education Consultant",
        "Tutor",
    ],

    # ── Engineering (Non-Software) ──
    "Engineering": [
        "Mechanical Engineer",
        "Electrical Engineer",
        "Civil Engineer",
        "Chemical Engineer",
        "Structural Engineer",
        "Environmental Engineer",
        "Industrial Engineer",
        "Aerospace Engineer",
        "Robotics Engineer",
        "Automotive Engineer",
    ],

    # ── Customer Service ──
    "Customer Support": [
        "Customer Service Representative",
        "Customer Success Manager",
        "Technical Support Specialist",
        "Call Center Agent",
        "Client Relations Manager",
        "Support Team Lead",
    ],

    # ── Media & Creative ──
    "Media & Creative": [
        "Video Producer",
        "Photographer",
        "Animator",
        "Art Director",
        "Creative Director",
        "Multimedia Specialist",
        "Podcast Producer",
    ],

    # ── Real Estate & Construction ──
    "Real Estate": [
        "Real Estate Agent",
        "Property Manager",
        "Real Estate Analyst",
        "Construction Manager",
        "Quantity Surveyor",
        "Architect",
        "Interior Designer",
    ],

    # ── Hospitality & Tourism ──
    "Hospitality": [
        "Hotel Manager",
        "Restaurant Manager",
        "Chef / Cook",
        "Event Coordinator",
        "Travel Consultant",
        "Front Desk Agent",
        "Tour Guide",
    ],

    # ── Catch-All ──
    "General": [
        "Administrative Assistant",
        "Executive Assistant",
        "Office Manager",
        "Data Entry Clerk",
        "Research Assistant",
        "Intern / Trainee",
        "Freelancer / Contractor",
        "Other",
    ],
}

# Reverse lookup: subcategory → category  (built once at import time)
_SUBCATEGORY_TO_CATEGORY: Dict[str, str] = {}
for _cat, _subs in JOB_TAXONOMY.items():
    for _sub in _subs:
        _SUBCATEGORY_TO_CATEGORY[_sub.lower()] = _cat

# Flat list of all categories
ALL_CATEGORIES: List[str] = list(JOB_TAXONOMY.keys())

# Flat list of all subcategories
ALL_SUBCATEGORIES: List[str] = [
    sub for subs in JOB_TAXONOMY.values() for sub in subs
]


# =============================================================================
# Taxonomy Helpers
# =============================================================================

def get_subcategories(category: str) -> List[str]:
    """Get subcategories for a given category."""
    return JOB_TAXONOMY.get(category, [])


def get_category_for_subcategory(subcategory: str) -> Optional[str]:
    """Reverse lookup: get parent category from a subcategory name."""
    return _SUBCATEGORY_TO_CATEGORY.get(subcategory.lower())


def classify_job_title(title: str) -> Tuple[str, str]:
    """
    Best-effort classification of a free-text job title into (category, subcategory).
    Uses keyword matching — for LLM-grade accuracy use the LLM classifier.
    """
    if not title:
        return ("General", "Other")

    title_lower = title.lower().strip()

    # ── Exact subcategory match ──
    for cat, subs in JOB_TAXONOMY.items():
        for sub in subs:
            if sub.lower() in title_lower or title_lower in sub.lower():
                return (cat, sub)

    # ── Keyword-based category mapping ──
    keyword_map: Dict[str, str] = {
        # Software Engineering
        "frontend": "Software Engineer",
        "front-end": "Software Engineer",
        "react": "Software Engineer",
        "angular": "Software Engineer",
        "vue": "Software Engineer",
        "backend": "Software Engineer",
        "back-end": "Software Engineer",
        "full.?stack": "Software Engineer",
        "fullstack": "Software Engineer",
        "software": "Software Engineer",
        "developer": "Software Engineer",
        "programmer": "Software Engineer",
        "mobile.?dev": "Software Engineer",
        "ios.?dev": "Software Engineer",
        "android.?dev": "Software Engineer",
        "flutter": "Software Engineer",
        "game.?dev": "Software Engineer",
        "embedded": "Software Engineer",
        "firmware": "Software Engineer",
        "web.?dev": "Software Engineer",
        # DevOps
        "devops": "DevOps Engineer",
        "sre": "DevOps Engineer",
        "site.?reliability": "DevOps Engineer",
        "infrastructure": "DevOps Engineer",
        "platform.?eng": "DevOps Engineer",
        "cloud.?eng": "DevOps Engineer",
        "kubernetes": "DevOps Engineer",
        "ci.?cd": "DevOps Engineer",
        # Data
        "data.?scien": "Data Scientist",
        "machine.?learn": "Data Scientist",
        "data.?analy": "Data Scientist",
        "data.?eng": "Data Scientist",
        "ml.?eng": "Data Scientist",
        "ai.?eng": "Data Scientist",
        "deep.?learn": "Data Scientist",
        "nlp": "Data Scientist",
        "computer.?vision": "Data Scientist",
        "bi.?analyst": "Data Scientist",
        "business.?intelligence": "Data Scientist",
        "mlops": "Data Scientist",
        # Cybersecurity
        "security": "Cybersecurity",
        "penetration": "Cybersecurity",
        "soc.?analyst": "Cybersecurity",
        "cyber": "Cybersecurity",
        "infosec": "Cybersecurity",
        # QA
        "qa": "QA / Testing",
        "test": "QA / Testing",
        "quality.?assurance": "QA / Testing",
        "sdet": "QA / Testing",
        "automation.?test": "QA / Testing",
        # IT
        "system.?admin": "IT & Systems",
        "network.?eng": "IT & Systems",
        "dba": "IT & Systems",
        "database.?admin": "IT & Systems",
        "helpdesk": "IT & Systems",
        "it.?support": "IT & Systems",
        "solutions.?arch": "IT & Systems",
        "enterprise.?arch": "IT & Systems",
        "cloud.?arch": "IT & Systems",
        "tech.?support": "IT & Systems",
        # Product
        "product.?man": "Product Manager",
        "product.?own": "Product Manager",
        # Design
        "ui.?des": "Design",
        "ux.?des": "Design",
        "graphic.?des": "Design",
        "visual.?des": "Design",
        "product.?des": "Design",
        "brand.?des": "Design",
        "motion.?des": "Design",
        "interaction.?des": "Design",
        # Project Management
        "project.?man": "Project Management",
        "scrum.?master": "Project Management",
        "agile.?coach": "Project Management",
        "program.?man": "Project Management",
        "delivery.?man": "Project Management",
        # Business Analyst
        "business.?analyst": "Business Analyst",
        "systems.?analyst": "Business Analyst",
        "requirements.?analyst": "Business Analyst",
        "process.?analyst": "Business Analyst",
        "functional.?consult": "Business Analyst",
        # Consulting
        "consult": "Consulting",
        # Marketing
        "marketing": "Marketing",
        "seo": "Marketing",
        "sem": "Marketing",
        "ppc": "Marketing",
        "social.?media": "Marketing",
        "brand.?man": "Marketing",
        "growth": "Marketing",
        "digital.?marketing": "Marketing",
        # Content
        "content.?writ": "Content & Communications",
        "copywrit": "Content & Communications",
        "technical.?writ": "Content & Communications",
        "communicat": "Content & Communications",
        "editor": "Content & Communications",
        "journalist": "Content & Communications",
        "pr.?spec": "Content & Communications",
        # Sales
        "sales": "Sales",
        "account.?exec": "Sales",
        "sdr": "Sales",
        "business.?dev": "Sales",
        "key.?account": "Sales",
        # Finance
        "financ": "Finance",
        "account": "Finance",
        "audit": "Finance",
        "tax": "Finance",
        "treasury": "Finance",
        "investment": "Finance",
        "risk.?analyst": "Finance",
        "fp.?a": "Finance",
        "bookkeep": "Finance",
        "controller": "Finance",
        # HR
        "hr": "HR",
        "human.?resource": "HR",
        "recruit": "HR",
        "talent.?acqui": "HR",
        "people": "HR",
        "compensat": "HR",
        "l.?d": "HR",
        # Legal
        "legal": "Legal",
        "lawyer": "Legal",
        "counsel": "Legal",
        "paralegal": "Legal",
        "complianc": "Legal",
        "contract.?man": "Legal",
        "patent": "Legal",
        "privacy": "Legal",
        # Operations
        "operat": "Operations",
        "supply.?chain": "Operations",
        "logistic": "Operations",
        "procurement": "Operations",
        "warehouse": "Operations",
        "inventory": "Operations",
        "vendor": "Operations",
        "facilit": "Operations",
        # Healthcare
        "doctor": "Healthcare",
        "physician": "Healthcare",
        "nurse": "Healthcare",
        "pharmacist": "Healthcare",
        "physio": "Healthcare",
        "clinical": "Healthcare",
        "biomedic": "Healthcare",
        "dentist": "Healthcare",
        "healthcare": "Healthcare",
        "medical": "Healthcare",
        # Education
        "teacher": "Education",
        "professor": "Education",
        "lecturer": "Education",
        "instructor": "Education",
        "tutor": "Education",
        "curriculum": "Education",
        "e.?learning": "Education",
        "education": "Education",
        # Engineering (non-software)
        "mechanical.?eng": "Engineering",
        "electrical.?eng": "Engineering",
        "civil.?eng": "Engineering",
        "chemical.?eng": "Engineering",
        "structural": "Engineering",
        "environmental.?eng": "Engineering",
        "industrial.?eng": "Engineering",
        "aerospace": "Engineering",
        "robotics": "Engineering",
        "automotive.?eng": "Engineering",
        # Customer Support
        "customer.?service": "Customer Support",
        "customer.?success": "Customer Support",
        "call.?center": "Customer Support",
        "client.?relat": "Customer Support",
        # Media
        "video.?prod": "Media & Creative",
        "photograph": "Media & Creative",
        "animat": "Media & Creative",
        "art.?dir": "Media & Creative",
        "creative.?dir": "Media & Creative",
        "multimedia": "Media & Creative",
        "podcast": "Media & Creative",
        # Real Estate
        "real.?estate": "Real Estate",
        "property.?man": "Real Estate",
        "architect": "Real Estate",
        "interior.?des": "Real Estate",
        "construction": "Real Estate",
        "quantity.?survey": "Real Estate",
        # Hospitality
        "hotel": "Hospitality",
        "restaurant": "Hospitality",
        "chef": "Hospitality",
        "cook": "Hospitality",
        "event.?coord": "Hospitality",
        "travel": "Hospitality",
        "front.?desk": "Hospitality",
        "tour.?guide": "Hospitality",
        "hospitality": "Hospitality",
        # General / Admin
        "admin": "General",
        "executive.?assist": "General",
        "office.?man": "General",
        "data.?entry": "General",
        "research.?assist": "General",
        "intern": "General",
        "trainee": "General",
    }

    for pattern, category in keyword_map.items():
        if re.search(pattern, title_lower):
            # Try to find best matching subcategory
            best_sub = _match_subcategory(title_lower, category)
            return (category, best_sub)

    return ("General", "Other")


def _match_subcategory(title_lower: str, category: str) -> str:
    """Find the best matching subcategory within a category."""
    subs = JOB_TAXONOMY.get(category, [])
    best_score = 0
    best_sub = subs[0] if subs else "Other"

    for sub in subs:
        sub_lower = sub.lower()
        # Check word overlap
        sub_words = set(re.findall(r'\w+', sub_lower))
        title_words = set(re.findall(r'\w+', title_lower))
        overlap = len(sub_words & title_words)
        # Bonus for substring match
        if sub_lower in title_lower or title_lower in sub_lower:
            overlap += 3
        if overlap > best_score:
            best_score = overlap
            best_sub = sub

    return best_sub


def get_taxonomy_prompt_text() -> str:
    """
    Generate a formatted string of the full taxonomy for use in LLM prompts.
    """
    lines = []
    for cat, subs in JOB_TAXONOMY.items():
        sub_list = ", ".join(subs)
        lines.append(f"- {cat}: [{sub_list}]")
    return "\n".join(lines)


def get_all_categories_with_subcategories() -> Dict[str, List[str]]:
    """Return the full taxonomy dict (used by API endpoints)."""
    return dict(JOB_TAXONOMY)

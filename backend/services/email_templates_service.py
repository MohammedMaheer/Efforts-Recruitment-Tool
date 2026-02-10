"""
Email Templates Service
Customizable email templates for candidate communications
Supports variable substitution and personalization
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TEMPLATES_PATH = Path(__file__).parent.parent / "data" / "email_templates"
TEMPLATES_PATH.mkdir(parents=True, exist_ok=True)


class EmailTemplatesService:
    """
    Manages email templates for candidate communications:
    - Application received
    - Interview invitations
    - Rejection letters
    - Offer letters
    - Follow-up reminders
    
    Supports variable substitution: {{candidate_name}}, {{job_title}}, etc.
    """
    
    # Default templates
    DEFAULT_TEMPLATES = {
        'application_received': {
            'name': 'Application Received',
            'subject': 'Application Received - {{job_title}} at {{company_name}}',
            'body': '''Dear {{candidate_name}},

Thank you for applying for the {{job_title}} position at {{company_name}}.

We have received your application and our team is currently reviewing it. If your qualifications match our requirements, we will contact you to schedule the next steps.

In the meantime, feel free to explore more about us at {{company_website}}.

Best regards,
{{recruiter_name}}
{{company_name}} Recruiting Team''',
            'category': 'acknowledgment',
        },
        
        'interview_invite': {
            'name': 'Interview Invitation',
            'subject': 'Interview Invitation - {{job_title}} at {{company_name}}',
            'body': '''Dear {{candidate_name}},

Congratulations! After reviewing your application for the {{job_title}} position, we would like to invite you for an interview.

Interview Details:
📅 Date: {{interview_date}}
⏰ Time: {{interview_time}}
📍 Location: {{interview_location}}
👤 Interviewer: {{interviewer_name}}
⏱️ Duration: {{interview_duration}}

{{#if is_video_call}}
Please join using this link: {{meeting_link}}
{{/if}}

Please confirm your availability by replying to this email.

What to prepare:
- Review the job description
- Prepare examples of your relevant experience
- Have questions ready about the role and team

If you need to reschedule, please let us know at least 24 hours in advance.

Looking forward to meeting you!

Best regards,
{{recruiter_name}}
{{company_name}}''',
            'category': 'interview',
        },
        
        'interview_reminder': {
            'name': 'Interview Reminder',
            'subject': 'Reminder: Your Interview Tomorrow - {{job_title}}',
            'body': '''Dear {{candidate_name}},

This is a friendly reminder about your upcoming interview:

📅 Date: {{interview_date}}
⏰ Time: {{interview_time}}
📍 Location: {{interview_location}}

{{#if is_video_call}}
Meeting Link: {{meeting_link}}
Please test your audio/video before the call.
{{/if}}

If anything has changed or you need to reschedule, please let us know immediately.

See you tomorrow!

Best regards,
{{recruiter_name}}''',
            'category': 'interview',
        },
        
        'rejection_after_review': {
            'name': 'Rejection - After Application Review',
            'subject': 'Update on Your Application - {{company_name}}',
            'body': '''Dear {{candidate_name}},

Thank you for your interest in the {{job_title}} position at {{company_name}} and for taking the time to apply.

After careful consideration, we have decided to move forward with other candidates whose experience more closely aligns with our current needs.

This decision does not reflect on your abilities or potential - we simply had to make difficult choices among many qualified applicants.

We encourage you to apply for future positions that match your skills. We'll keep your resume on file and reach out if a suitable opportunity arises.

We wish you the best in your job search and future career.

Best regards,
{{recruiter_name}}
{{company_name}} Recruiting Team''',
            'category': 'rejection',
        },
        
        'rejection_after_interview': {
            'name': 'Rejection - After Interview',
            'subject': 'Update on Your Interview - {{company_name}}',
            'body': '''Dear {{candidate_name}},

Thank you for taking the time to interview for the {{job_title}} position at {{company_name}}. We enjoyed learning more about your background and experience.

After thorough consideration, we have decided to proceed with another candidate whose experience more closely aligns with our current needs.

{{#if feedback}}
Feedback: {{feedback}}
{{/if}}

This was a difficult decision, and we were impressed by your qualifications. We encourage you to apply for future opportunities at {{company_name}}.

Thank you again for your interest in joining our team.

Best regards,
{{recruiter_name}}
{{company_name}}''',
            'category': 'rejection',
        },
        
        'offer_letter': {
            'name': 'Job Offer',
            'subject': 'Job Offer - {{job_title}} at {{company_name}}',
            'body': '''Dear {{candidate_name}},

We are thrilled to offer you the position of {{job_title}} at {{company_name}}!

After our conversations, we believe you will be a fantastic addition to our team. Below are the details of our offer:

Position Details:
• Title: {{job_title}}
• Department: {{department}}
• Manager: {{manager_name}}
• Start Date: {{start_date}}

Compensation:
• Base Salary: {{salary}} per year
• Bonus: {{bonus}}
• Benefits: {{benefits}}

{{#if equity}}
Equity: {{equity}}
{{/if}}

Please review the attached offer letter for complete details. To accept this offer, please sign and return by {{offer_deadline}}.

If you have any questions, please don't hesitate to reach out.

We're excited about the possibility of you joining {{company_name}}!

Best regards,
{{recruiter_name}}
{{company_name}}''',
            'category': 'offer',
        },
        
        'follow_up_no_response': {
            'name': 'Follow Up - No Response',
            'subject': 'Following Up - {{job_title}} Position',
            'body': '''Hi {{candidate_name}},

I wanted to follow up on my previous email regarding the {{job_title}} position at {{company_name}}.

I understand you may be busy, but I wanted to ensure you received my message. We're still interested in speaking with you about this opportunity.

If you're no longer interested or have accepted another offer, no worries at all - just let me know and I'll update my records.

If you'd like to discuss further, please reply to this email or {{calendar_link}}.

Looking forward to hearing from you!

Best regards,
{{recruiter_name}}''',
            'category': 'follow_up',
        },
        
        'check_in_passive': {
            'name': 'Check In - Passive Candidate',
            'subject': 'Exciting Opportunity at {{company_name}}',
            'body': '''Hi {{candidate_name}},

I hope this message finds you well! I came across your profile and was impressed by your experience in {{candidate_specialty}}.

I'm reaching out because we have an exciting {{job_title}} opportunity at {{company_name}} that I think could be a great fit for your background.

About the role:
{{job_highlights}}

About {{company_name}}:
{{company_highlights}}

Would you be open to a brief call to discuss? Even if the timing isn't right, I'd love to connect and share more about what we're building.

Let me know what works for you!

Best,
{{recruiter_name}}''',
            'category': 'outreach',
        },
        
        'schedule_link': {
            'name': 'Schedule Interview',
            'subject': 'Schedule Your Interview - {{job_title}}',
            'body': '''Hi {{candidate_name}},

Great news! We'd like to move forward with your application for {{job_title}}.

Please use the link below to schedule a convenient time for your interview:

📅 {{scheduling_link}}

The interview will be approximately {{interview_duration}} with {{interviewer_name}}.

If none of the available times work for you, please reply with a few alternatives that work on your end.

Looking forward to speaking with you!

Best regards,
{{recruiter_name}}
{{company_name}}''',
            'category': 'interview',
        },

        'shortlist_notification': {
            'name': 'Shortlisted - Next Round Notification',
            'subject': 'Great News! You\'ve Been Shortlisted - {{company_name}}',
            'body': '''Dear {{candidate_name}},

We are pleased to inform you that after careful review of your application, you have been shortlisted for the next round of our selection process{{#if job_title}} for the {{job_title}} position{{/if}} at {{company_name}}.

Your qualifications and experience stood out among many talented applicants, and we are excited to learn more about you.

What happens next:
📋 Our team will reach out shortly with details about the next steps
📅 You may be invited for an interview or assessment
📧 Please keep an eye on your inbox for further communication

In the meantime, if you have any questions, please don\'t hesitate to reply to this email.

We look forward to continuing the process with you!

Best regards,
{{recruiter_name}}
{{company_name}} Recruiting Team''',
            'category': 'shortlist',
        },
    }
    
    # Available variables
    VARIABLES = {
        'candidate_name': 'Candidate full name',
        'candidate_email': 'Candidate email address',
        'candidate_phone': 'Candidate phone number',
        'job_title': 'Job position title',
        'company_name': 'Your company name',
        'company_website': 'Company website URL',
        'recruiter_name': 'Recruiter/sender name',
        'interview_date': 'Interview date',
        'interview_time': 'Interview time',
        'interview_location': 'Interview location or meeting link',
        'interview_duration': 'Expected interview duration',
        'interviewer_name': 'Name of interviewer',
        'meeting_link': 'Video call/meeting link',
        'salary': 'Offered salary',
        'start_date': 'Proposed start date',
        'offer_deadline': 'Deadline to accept offer',
        'scheduling_link': 'Calendly or scheduling link',
        'calendar_link': 'Calendar booking link',
    }
    
    def __init__(self):
        self.templates = {}
        self.custom_templates = {}
        self._load_templates()
    
    def _load_templates(self):
        """Load default and custom templates"""
        # Load defaults
        self.templates = self.DEFAULT_TEMPLATES.copy()
        
        # Load custom templates from disk
        custom_file = TEMPLATES_PATH / "custom_templates.json"
        if custom_file.exists():
            try:
                with open(custom_file, 'r') as f:
                    self.custom_templates = json.load(f)
                self.templates.update(self.custom_templates)
                logger.info(f"Loaded {len(self.custom_templates)} custom templates")
            except Exception as e:
                logger.warning(f"Could not load custom templates: {e}")
    
    def _save_custom_templates(self):
        """Save custom templates to disk"""
        custom_file = TEMPLATES_PATH / "custom_templates.json"
        try:
            with open(custom_file, 'w') as f:
                json.dump(self.custom_templates, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save templates: {e}")
    
    def get_template(self, template_id: str) -> Optional[Dict]:
        """Get a template by ID"""
        return self.templates.get(template_id)
    
    def get_all_templates(self, category: str = None) -> Dict[str, Dict]:
        """Get all templates, optionally filtered by category"""
        if category:
            return {
                k: v for k, v in self.templates.items() 
                if v.get('category') == category
            }
        return self.templates
    
    def create_template(self, template_id: str, template: Dict) -> Dict:
        """Create a new custom template"""
        required = ['name', 'subject', 'body']
        if not all(k in template for k in required):
            raise ValueError(f"Template must include: {required}")
        
        template['created_at'] = datetime.now().isoformat()
        template['is_custom'] = True
        
        self.custom_templates[template_id] = template
        self.templates[template_id] = template
        self._save_custom_templates()
        
        return template
    
    def update_template(self, template_id: str, updates: Dict) -> Dict:
        """Update an existing template"""
        if template_id not in self.templates:
            raise ValueError(f"Template {template_id} not found")
        
        template = self.templates[template_id].copy()
        template.update(updates)
        template['updated_at'] = datetime.now().isoformat()
        
        self.templates[template_id] = template
        
        # If it was a default, it becomes custom now
        self.custom_templates[template_id] = template
        self._save_custom_templates()
        
        return template
    
    def delete_template(self, template_id: str) -> bool:
        """Delete a custom template"""
        if template_id not in self.custom_templates:
            return False  # Can't delete default templates
        
        del self.custom_templates[template_id]
        
        # Restore default if exists
        if template_id in self.DEFAULT_TEMPLATES:
            self.templates[template_id] = self.DEFAULT_TEMPLATES[template_id]
        else:
            del self.templates[template_id]
        
        self._save_custom_templates()
        return True
    
    def render_template(self, template_id: str, variables: Dict) -> Dict:
        """
        Render a template with variable substitution
        
        Args:
            template_id: Template ID
            variables: Dict of variable values
        
        Returns:
            Dict with rendered subject and body
        """
        template = self.get_template(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")
        
        subject = self._substitute_variables(template['subject'], variables)
        body = self._substitute_variables(template['body'], variables)
        
        # Handle conditional blocks {{#if var}}...{{/if}}
        body = self._process_conditionals(body, variables)
        
        return {
            'subject': subject,
            'body': body,
            'from_template': template_id,
            'rendered_at': datetime.now().isoformat()
        }
    
    def _substitute_variables(self, text: str, variables: Dict) -> str:
        """Replace {{variable}} placeholders with values"""
        def replace(match):
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))
        
        return re.sub(r'\{\{(\w+)\}\}', replace, text)
    
    def _process_conditionals(self, text: str, variables: Dict) -> str:
        """Process {{#if var}}content{{/if}} blocks"""
        pattern = r'\{\{#if\s+(\w+)\}\}(.*?)\{\{/if\}\}'
        
        def replace_conditional(match):
            var_name = match.group(1)
            content = match.group(2)
            
            if variables.get(var_name):
                return self._substitute_variables(content, variables)
            return ''
        
        return re.sub(pattern, replace_conditional, text, flags=re.DOTALL)
    
    def preview_template(self, template_id: str) -> Dict:
        """Preview template with sample data"""
        sample_variables = {
            'candidate_name': 'John Smith',
            'candidate_email': 'john.smith@email.com',
            'job_title': 'Senior Software Engineer',
            'company_name': 'TechCorp Inc.',
            'company_website': 'https://techcorp.com',
            'recruiter_name': 'Sarah Johnson',
            'interview_date': 'Monday, March 15, 2026',
            'interview_time': '10:00 AM PST',
            'interview_location': 'Google Meet',
            'interview_duration': '45 minutes',
            'interviewer_name': 'Michael Chen',
            'meeting_link': 'https://meet.google.com/abc-defg-hij',
            'salary': '$150,000',
            'start_date': 'April 1, 2026',
            'offer_deadline': 'March 20, 2026',
            'scheduling_link': 'https://calendly.com/recruiter/interview',
            'is_video_call': True,
            'feedback': 'Strong technical skills, would benefit from more leadership experience.',
        }
        
        return self.render_template(template_id, sample_variables)
    
    def get_template_categories(self) -> List[Dict]:
        """Get template categories with counts"""
        categories = {}
        
        for template in self.templates.values():
            cat = template.get('category', 'other')
            if cat not in categories:
                categories[cat] = {'name': cat, 'count': 0, 'templates': []}
            categories[cat]['count'] += 1
        
        return list(categories.values())
    
    def get_available_variables(self) -> Dict[str, str]:
        """Get list of available template variables"""
        return self.VARIABLES.copy()
    
    def duplicate_template(self, template_id: str, new_id: str) -> Dict:
        """Duplicate a template with a new ID"""
        original = self.get_template(template_id)
        if not original:
            raise ValueError(f"Template {template_id} not found")
        
        new_template = original.copy()
        new_template['name'] = f"{original['name']} (Copy)"
        new_template['is_custom'] = True
        new_template['created_at'] = datetime.now().isoformat()
        
        return self.create_template(new_id, new_template)


# Singleton
_templates_service = None

def get_templates_service() -> EmailTemplatesService:
    global _templates_service
    if _templates_service is None:
        _templates_service = EmailTemplatesService()
    return _templates_service

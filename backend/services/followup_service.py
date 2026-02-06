"""
Automated Follow-up & Drip Campaign Service
Manages automated follow-up sequences for candidates
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent.parent / "data" / "campaigns"
DATA_PATH.mkdir(parents=True, exist_ok=True)


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepType(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    TASK = "task"  # Create task for recruiter
    WEBHOOK = "webhook"


class AutomatedFollowUpService:
    """
    Drip campaign and automated follow-up system:
    - Multi-step email/SMS sequences
    - Conditional branching (if responded, stop campaign)
    - Time-based triggers
    - Performance tracking
    """
    
    # Default campaign templates
    DEFAULT_CAMPAIGNS = {
        'new_applicant_nurture': {
            'name': 'New Applicant Nurture',
            'description': 'Follow up with new applicants who haven\'t been contacted',
            'trigger': 'new_application',
            'steps': [
                {
                    'delay_days': 0,
                    'delay_hours': 1,
                    'type': 'email',
                    'template': 'application_received',
                    'subject': 'Application Received - {{job_title}}',
                },
                {
                    'delay_days': 3,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'follow_up_no_response',
                    'subject': 'Following Up on Your Application',
                    'condition': 'no_response',
                },
                {
                    'delay_days': 7,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'final_follow_up',
                    'subject': 'Last Follow Up - {{job_title}}',
                    'condition': 'no_response',
                },
            ],
            'stop_conditions': ['responded', 'status_changed', 'interview_scheduled'],
        },
        
        'interview_no_show': {
            'name': 'Interview No-Show Recovery',
            'description': 'Re-engage candidates who missed their interview',
            'trigger': 'interview_no_show',
            'steps': [
                {
                    'delay_days': 0,
                    'delay_hours': 2,
                    'type': 'email',
                    'template': 'missed_interview',
                    'subject': 'We Missed You Today - Reschedule?',
                },
                {
                    'delay_days': 0,
                    'delay_hours': 2,
                    'type': 'sms',
                    'message': 'Hi {name}, we missed you at today\'s interview with {company}. Reply to reschedule.',
                    'condition': 'has_phone',
                },
                {
                    'delay_days': 2,
                    'delay_hours': 0,
                    'type': 'task',
                    'task': 'Review no-show candidate: {{candidate_name}}',
                    'condition': 'no_response',
                },
            ],
            'stop_conditions': ['responded', 'rescheduled'],
        },
        
        'offer_follow_up': {
            'name': 'Offer Follow-Up',
            'description': 'Follow up after sending job offer',
            'trigger': 'offer_sent',
            'steps': [
                {
                    'delay_days': 2,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'offer_check_in',
                    'subject': 'Questions About Your Offer?',
                    'condition': 'no_response',
                },
                {
                    'delay_days': 4,
                    'delay_hours': 0,
                    'type': 'sms',
                    'message': 'Hi {name}, friendly reminder about the {company} offer. Deadline is approaching. Questions? Reply here.',
                    'condition': 'no_response',
                },
                {
                    'delay_days': 6,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'offer_deadline_reminder',
                    'subject': 'Offer Deadline Tomorrow - {{company_name}}',
                    'condition': 'no_response',
                },
            ],
            'stop_conditions': ['offer_accepted', 'offer_declined', 'responded'],
        },
        
        'passive_candidate_nurture': {
            'name': 'Passive Candidate Nurture',
            'description': 'Long-term nurture for passive candidates',
            'trigger': 'manual',
            'steps': [
                {
                    'delay_days': 0,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'check_in_passive',
                    'subject': 'Opportunity at {{company_name}}',
                },
                {
                    'delay_days': 7,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'company_news',
                    'subject': 'Exciting Updates from {{company_name}}',
                    'condition': 'no_response',
                },
                {
                    'delay_days': 30,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'check_in_passive',
                    'subject': 'Still Interested in {{job_title}}?',
                    'condition': 'no_response',
                },
                {
                    'delay_days': 60,
                    'delay_hours': 0,
                    'type': 'task',
                    'task': 'Review passive candidate engagement: {{candidate_name}}',
                },
            ],
            'stop_conditions': ['responded', 'applied', 'unsubscribed'],
        },
        
        'rejected_candidate_nurture': {
            'name': 'Rejected Candidate Talent Pool',
            'description': 'Keep rejected candidates engaged for future opportunities',
            'trigger': 'rejection_sent',
            'steps': [
                {
                    'delay_days': 30,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'talent_pool_invite',
                    'subject': 'Join Our Talent Network - {{company_name}}',
                },
                {
                    'delay_days': 90,
                    'delay_hours': 0,
                    'type': 'email',
                    'template': 'new_opportunities',
                    'subject': 'New Opportunities at {{company_name}}',
                    'condition': 'talent_pool_member',
                },
            ],
            'stop_conditions': ['unsubscribed', 'rehired'],
        },
    }
    
    def __init__(self):
        self.campaigns = {}
        self.active_enrollments = {}  # candidate_id -> enrollment data
        self.email_service = None
        self.sms_service = None
        self._load_campaigns()
        self._load_enrollments()
    
    def _load_campaigns(self):
        """Load campaign configurations"""
        self.campaigns = self.DEFAULT_CAMPAIGNS.copy()
        
        custom_file = DATA_PATH / "custom_campaigns.json"
        if custom_file.exists():
            try:
                with open(custom_file, 'r') as f:
                    custom = json.load(f)
                self.campaigns.update(custom)
            except Exception as e:
                logger.warning(f"Could not load custom campaigns: {e}")
    
    def _save_campaigns(self):
        """Save custom campaigns"""
        custom = {k: v for k, v in self.campaigns.items() 
                  if k not in self.DEFAULT_CAMPAIGNS}
        
        try:
            with open(DATA_PATH / "custom_campaigns.json", 'w') as f:
                json.dump(custom, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save campaigns: {e}")
    
    def _load_enrollments(self):
        """Load active enrollments"""
        enrollments_file = DATA_PATH / "enrollments.json"
        if enrollments_file.exists():
            try:
                with open(enrollments_file, 'r') as f:
                    self.active_enrollments = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load enrollments: {e}")
    
    def _save_enrollments(self):
        """Save active enrollments"""
        try:
            with open(DATA_PATH / "enrollments.json", 'w') as f:
                json.dump(self.active_enrollments, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Could not save enrollments: {e}")
    
    def get_campaign(self, campaign_id: str) -> Optional[Dict]:
        """Get campaign by ID"""
        return self.campaigns.get(campaign_id)
    
    def get_all_campaigns(self) -> Dict[str, Dict]:
        """Get all campaigns"""
        return self.campaigns
    
    def create_campaign(self, campaign_id: str, campaign: Dict) -> Dict:
        """Create a new campaign"""
        required = ['name', 'steps']
        if not all(k in campaign for k in required):
            raise ValueError(f"Campaign must include: {required}")
        
        campaign['created_at'] = datetime.now().isoformat()
        campaign['is_custom'] = True
        
        self.campaigns[campaign_id] = campaign
        self._save_campaigns()
        
        return campaign
    
    def update_campaign(self, campaign_id: str, updates: Dict) -> Dict:
        """Update existing campaign"""
        if campaign_id not in self.campaigns:
            raise ValueError(f"Campaign not found: {campaign_id}")
        
        self.campaigns[campaign_id].update(updates)
        self.campaigns[campaign_id]['updated_at'] = datetime.now().isoformat()
        self._save_campaigns()
        
        return self.campaigns[campaign_id]
    
    def delete_campaign(self, campaign_id: str) -> bool:
        """Delete a custom campaign"""
        if campaign_id in self.DEFAULT_CAMPAIGNS:
            return False  # Can't delete default
        
        if campaign_id in self.campaigns:
            del self.campaigns[campaign_id]
            self._save_campaigns()
            return True
        return False
    
    def enroll_candidate(
        self,
        candidate: Dict,
        campaign_id: str,
        variables: Dict = None
    ) -> Dict:
        """
        Enroll a candidate in a drip campaign
        
        Args:
            candidate: Candidate dict with id, email, name, phone
            campaign_id: Campaign to enroll in
            variables: Template variables for personalization
        """
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return {'status': 'error', 'error': f'Campaign not found: {campaign_id}'}
        
        candidate_id = candidate.get('id')
        if not candidate_id:
            return {'status': 'error', 'error': 'Candidate ID required'}
        
        # Check if already enrolled in this campaign
        enrollment_key = f"{candidate_id}_{campaign_id}"
        if enrollment_key in self.active_enrollments:
            existing = self.active_enrollments[enrollment_key]
            if existing['status'] == CampaignStatus.ACTIVE:
                return {'status': 'error', 'error': 'Already enrolled in this campaign'}
        
        # Create enrollment
        enrollment = {
            'candidate_id': candidate_id,
            'candidate_email': candidate.get('email'),
            'candidate_name': candidate.get('name'),
            'candidate_phone': candidate.get('phone'),
            'campaign_id': campaign_id,
            'campaign_name': campaign['name'],
            'status': CampaignStatus.ACTIVE,
            'current_step': 0,
            'enrolled_at': datetime.now().isoformat(),
            'variables': variables or {},
            'history': [],
            'next_step_at': self._calculate_next_step_time(campaign['steps'][0]),
        }
        
        self.active_enrollments[enrollment_key] = enrollment
        self._save_enrollments()
        
        logger.info(f"📬 Enrolled {candidate.get('name')} in campaign: {campaign['name']}")
        
        return {
            'status': 'success',
            'enrollment_id': enrollment_key,
            'campaign': campaign['name'],
            'next_step_at': enrollment['next_step_at'],
        }
    
    def _calculate_next_step_time(self, step: Dict, from_time: datetime = None) -> str:
        """Calculate when next step should execute"""
        from_time = from_time or datetime.now()
        
        delay = timedelta(
            days=step.get('delay_days', 0),
            hours=step.get('delay_hours', 0)
        )
        
        next_time = from_time + delay
        return next_time.isoformat()
    
    def unenroll_candidate(
        self,
        candidate_id: str,
        campaign_id: str = None,
        reason: str = 'manual'
    ) -> Dict:
        """
        Remove candidate from campaign(s)
        
        Args:
            candidate_id: Candidate ID
            campaign_id: Specific campaign (None = all campaigns)
            reason: Reason for unenrollment
        """
        unenrolled = []
        
        for key, enrollment in list(self.active_enrollments.items()):
            if enrollment['candidate_id'] != candidate_id:
                continue
            
            if campaign_id and enrollment['campaign_id'] != campaign_id:
                continue
            
            enrollment['status'] = CampaignStatus.CANCELLED
            enrollment['cancelled_at'] = datetime.now().isoformat()
            enrollment['cancel_reason'] = reason
            
            unenrolled.append(enrollment['campaign_name'])
        
        self._save_enrollments()
        
        return {
            'status': 'success',
            'unenrolled_from': unenrolled,
            'reason': reason
        }
    
    async def process_due_steps(self) -> Dict:
        """
        Process all due campaign steps
        Should be called periodically (e.g., every 5 minutes)
        """
        now = datetime.now()
        processed = 0
        errors = []
        
        for enrollment_key, enrollment in list(self.active_enrollments.items()):
            if enrollment['status'] != CampaignStatus.ACTIVE:
                continue
            
            next_step_at = datetime.fromisoformat(enrollment['next_step_at'])
            if next_step_at > now:
                continue  # Not due yet
            
            # Process step
            campaign = self.get_campaign(enrollment['campaign_id'])
            if not campaign:
                continue
            
            current_step_idx = enrollment['current_step']
            if current_step_idx >= len(campaign['steps']):
                # Campaign completed
                enrollment['status'] = CampaignStatus.COMPLETED
                enrollment['completed_at'] = now.isoformat()
                continue
            
            step = campaign['steps'][current_step_idx]
            
            # Check step condition
            if not self._check_step_condition(step, enrollment):
                # Skip this step
                enrollment['history'].append({
                    'step': current_step_idx,
                    'action': 'skipped',
                    'reason': f"Condition not met: {step.get('condition')}",
                    'timestamp': now.isoformat()
                })
                enrollment['current_step'] += 1
                
                if enrollment['current_step'] < len(campaign['steps']):
                    enrollment['next_step_at'] = self._calculate_next_step_time(
                        campaign['steps'][enrollment['current_step']]
                    )
                continue
            
            # Execute step
            try:
                result = await self._execute_step(step, enrollment)
                
                enrollment['history'].append({
                    'step': current_step_idx,
                    'type': step['type'],
                    'action': 'executed',
                    'result': result.get('status'),
                    'timestamp': now.isoformat()
                })
                
                processed += 1
                
            except Exception as e:
                logger.error(f"Step execution error: {e}")
                errors.append({
                    'enrollment': enrollment_key,
                    'step': current_step_idx,
                    'error': str(e)
                })
            
            # Move to next step
            enrollment['current_step'] += 1
            
            if enrollment['current_step'] < len(campaign['steps']):
                enrollment['next_step_at'] = self._calculate_next_step_time(
                    campaign['steps'][enrollment['current_step']]
                )
            else:
                enrollment['status'] = CampaignStatus.COMPLETED
                enrollment['completed_at'] = now.isoformat()
        
        self._save_enrollments()
        
        return {
            'processed': processed,
            'errors': len(errors),
            'error_details': errors[:5]  # First 5 errors
        }
    
    def _check_step_condition(self, step: Dict, enrollment: Dict) -> bool:
        """Check if step condition is met"""
        condition = step.get('condition')
        
        if not condition:
            return True
        
        if condition == 'no_response':
            # Check if candidate has responded (simplified)
            return not enrollment.get('has_responded', False)
        
        if condition == 'has_phone':
            return bool(enrollment.get('candidate_phone'))
        
        if condition == 'has_email':
            return bool(enrollment.get('candidate_email'))
        
        # Default: execute
        return True
    
    async def _execute_step(self, step: Dict, enrollment: Dict) -> Dict:
        """Execute a campaign step"""
        step_type = step.get('type')
        variables = {
            'name': enrollment.get('candidate_name', '').split()[0],
            'candidate_name': enrollment.get('candidate_name'),
            'candidate_email': enrollment.get('candidate_email'),
            **enrollment.get('variables', {})
        }
        
        if step_type == StepType.EMAIL:
            # Send email
            if self.email_service:
                return await self.email_service.send_template_email(
                    to_email=enrollment['candidate_email'],
                    template=step.get('template'),
                    variables=variables,
                    subject=self._substitute_vars(step.get('subject', ''), variables)
                )
            return {'status': 'skipped', 'reason': 'Email service not available'}
        
        elif step_type == StepType.SMS:
            # Send SMS
            if self.sms_service and enrollment.get('candidate_phone'):
                message = self._substitute_vars(step.get('message', ''), variables)
                return await self.sms_service.send_sms(
                    to_phone=enrollment['candidate_phone'],
                    message=message,
                    candidate_id=enrollment['candidate_id']
                )
            return {'status': 'skipped', 'reason': 'SMS service not available or no phone'}
        
        elif step_type == StepType.TASK:
            # Create task for recruiter
            task_description = self._substitute_vars(step.get('task', ''), variables)
            return {
                'status': 'success',
                'type': 'task_created',
                'task': task_description
            }
        
        elif step_type == StepType.WEBHOOK:
            # Call webhook (for integrations)
            return {'status': 'skipped', 'reason': 'Webhook not implemented'}
        
        return {'status': 'unknown_type'}
    
    def _substitute_vars(self, text: str, variables: Dict) -> str:
        """Substitute {{variable}} placeholders"""
        for key, value in variables.items():
            text = text.replace(f'{{{{{key}}}}}', str(value))
            text = text.replace(f'{{{key}}}', str(value))
        return text
    
    def mark_responded(self, candidate_id: str, campaign_id: str = None):
        """Mark that candidate has responded (stops campaign)"""
        for key, enrollment in self.active_enrollments.items():
            if enrollment['candidate_id'] != candidate_id:
                continue
            
            if campaign_id and enrollment['campaign_id'] != campaign_id:
                continue
            
            enrollment['has_responded'] = True
            enrollment['responded_at'] = datetime.now().isoformat()
            
            # Check stop conditions
            campaign = self.get_campaign(enrollment['campaign_id'])
            if campaign and 'responded' in campaign.get('stop_conditions', []):
                enrollment['status'] = CampaignStatus.COMPLETED
                enrollment['completed_reason'] = 'Candidate responded'
        
        self._save_enrollments()
    
    def get_candidate_enrollments(self, candidate_id: str) -> List[Dict]:
        """Get all enrollments for a candidate"""
        return [
            e for e in self.active_enrollments.values()
            if e['candidate_id'] == candidate_id
        ]
    
    def get_campaign_stats(self, campaign_id: str) -> Dict:
        """Get statistics for a campaign"""
        enrollments = [
            e for e in self.active_enrollments.values()
            if e['campaign_id'] == campaign_id
        ]
        
        return {
            'campaign_id': campaign_id,
            'total_enrolled': len(enrollments),
            'active': len([e for e in enrollments if e['status'] == CampaignStatus.ACTIVE]),
            'completed': len([e for e in enrollments if e['status'] == CampaignStatus.COMPLETED]),
            'cancelled': len([e for e in enrollments if e['status'] == CampaignStatus.CANCELLED]),
            'responded': len([e for e in enrollments if e.get('has_responded')]),
        }
    
    def get_all_stats(self) -> Dict:
        """Get overall campaign statistics"""
        return {
            'total_campaigns': len(self.campaigns),
            'total_enrollments': len(self.active_enrollments),
            'active_enrollments': len([
                e for e in self.active_enrollments.values()
                if e['status'] == CampaignStatus.ACTIVE
            ]),
            'campaigns': {
                cid: self.get_campaign_stats(cid)
                for cid in self.campaigns.keys()
            }
        }
    
    def set_services(self, email_service=None, sms_service=None):
        """Set email and SMS services for step execution"""
        self.email_service = email_service
        self.sms_service = sms_service


# Singleton
_followup_service = None

def get_followup_service() -> AutomatedFollowUpService:
    global _followup_service
    if _followup_service is None:
        _followup_service = AutomatedFollowUpService()
    return _followup_service


# Background task runner
async def run_campaign_processor(interval_seconds: int = 300):
    """
    Background task to process campaign steps
    Run this in a background task/worker
    """
    service = get_followup_service()
    
    while True:
        try:
            result = await service.process_due_steps()
            if result['processed'] > 0:
                logger.info(f"📬 Processed {result['processed']} campaign steps")
            if result['errors'] > 0:
                logger.warning(f"⚠️ {result['errors']} campaign step errors")
        except Exception as e:
            logger.error(f"Campaign processor error: {e}")
        
        await asyncio.sleep(interval_seconds)

"""
SMS Notifications Service
Twilio integration for urgent candidate communications
"""
import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SMSNotificationService:
    """
    Twilio-powered SMS notifications for recruiting:
    - Interview reminders
    - Urgent updates
    - Offer notifications
    - Two-way messaging support
    """
    
    # SMS Templates (160 char limit for single SMS)
    TEMPLATES = {
        'interview_reminder': {
            'message': "Hi {name}! Reminder: Your interview with {company} is tomorrow at {time}. Location: {location}. Reply CONFIRM or RESCHEDULE.",
            'type': 'reminder'
        },
        'interview_today': {
            'message': "Hi {name}! Your interview with {company} is today at {time}. Good luck! Reply if you need to reach us.",
            'type': 'reminder'
        },
        'offer_sent': {
            'message': "Great news {name}! {company} has sent you a job offer. Please check your email for details. Reply with questions.",
            'type': 'notification'
        },
        'document_request': {
            'message': "Hi {name}, {company} needs additional documents for your application. Please check your email. Questions? Reply here.",
            'type': 'action_required'
        },
        'status_update': {
            'message': "Hi {name}, update on your {company} application: {status}. Check email for details or reply with questions.",
            'type': 'update'
        },
        'quick_question': {
            'message': "Hi {name}, quick question from {company} recruiting: {question} Reply YES or NO.",
            'type': 'question'
        },
        'meeting_link': {
            'message': "Hi {name}! Your {company} interview is starting. Join here: {link}",
            'type': 'urgent'
        },
    }
    
    def __init__(self):
        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.phone_number = os.getenv('TWILIO_PHONE_NUMBER')
        self.client = None
        self._init_twilio()
        
        # Message history (in production, store in database)
        self.message_log = []
    
    def _init_twilio(self):
        """Initialize Twilio client"""
        if self.account_sid and self.auth_token:
            try:
                from twilio.rest import Client
                self.client = Client(self.account_sid, self.auth_token)
                logger.info("✅ Twilio SMS service initialized")
            except ImportError:
                logger.warning("Twilio package not installed. Run: pip install twilio")
            except Exception as e:
                logger.error(f"Twilio init error: {e}")
    
    def is_configured(self) -> bool:
        """Check if Twilio is properly configured"""
        return self.client is not None and self.phone_number is not None
    
    def normalize_phone(self, phone: str) -> Optional[str]:
        """
        Normalize phone number to E.164 format
        E.g., "(555) 123-4567" -> "+15551234567"
        """
        if not phone:
            return None
        
        # Remove all non-digit characters
        digits = re.sub(r'\D', '', phone)
        
        # Handle different formats
        if len(digits) == 10:
            # US number without country code
            return f"+1{digits}"
        elif len(digits) == 11 and digits.startswith('1'):
            # US number with country code
            return f"+{digits}"
        elif len(digits) >= 10:
            # International - assume has country code
            return f"+{digits}"
        else:
            logger.warning(f"Invalid phone number format: {phone}")
            return None
    
    async def send_sms(
        self,
        to_phone: str,
        message: str,
        candidate_id: str = None
    ) -> Dict:
        """
        Send a single SMS message
        
        Args:
            to_phone: Recipient phone number
            message: Message content (max 1600 chars, will be split if needed)
            candidate_id: Optional candidate ID for tracking
        
        Returns:
            Dict with status and message SID
        """
        if not self.is_configured():
            return {
                'status': 'error',
                'error': 'Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER'
            }
        
        normalized = self.normalize_phone(to_phone)
        if not normalized:
            return {
                'status': 'error',
                'error': f'Invalid phone number: {to_phone}'
            }
        
        try:
            # Twilio handles message splitting for long messages
            sms = await asyncio.to_thread(
                self.client.messages.create,
                body=message,
                from_=self.phone_number,
                to=normalized
            )
            
            # Log message
            log_entry = {
                'sid': sms.sid,
                'to': normalized,
                'message': message[:100] + '...' if len(message) > 100 else message,
                'status': sms.status,
                'timestamp': datetime.now().isoformat(),
                'candidate_id': candidate_id,
            }
            self.message_log.append(log_entry)
            
            logger.info(f"📱 SMS sent to {normalized}: {sms.sid}")
            
            return {
                'status': 'success',
                'sid': sms.sid,
                'to': normalized,
                'segments': (len(message) // 160) + 1,
            }
            
        except Exception as e:
            logger.error(f"SMS send error: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
    
    async def send_template_sms(
        self,
        to_phone: str,
        template_name: str,
        variables: Dict,
        candidate_id: str = None
    ) -> Dict:
        """
        Send SMS using a predefined template
        
        Args:
            to_phone: Recipient phone number
            template_name: Name of template to use
            variables: Dict of variables to substitute
            candidate_id: Optional candidate ID
        """
        template = self.TEMPLATES.get(template_name)
        if not template:
            return {
                'status': 'error',
                'error': f'Template not found: {template_name}'
            }
        
        # Substitute variables
        message = template['message']
        for key, value in variables.items():
            message = message.replace(f'{{{key}}}', str(value))
        
        # Check for unreplaced variables
        if '{' in message:
            missing = re.findall(r'\{(\w+)\}', message)
            return {
                'status': 'error',
                'error': f'Missing variables: {missing}'
            }
        
        return await self.send_sms(to_phone, message, candidate_id)
    
    async def send_interview_reminder(
        self,
        candidate: Dict,
        interview_time: str,
        location: str,
        company_name: str
    ) -> Dict:
        """Send interview reminder SMS"""
        return await self.send_template_sms(
            to_phone=candidate.get('phone', ''),
            template_name='interview_reminder',
            variables={
                'name': candidate.get('name', '').split()[0],  # First name
                'company': company_name,
                'time': interview_time,
                'location': location[:50]  # Truncate long locations
            },
            candidate_id=candidate.get('id')
        )
    
    async def send_meeting_link(
        self,
        candidate: Dict,
        meeting_link: str,
        company_name: str
    ) -> Dict:
        """Send meeting link SMS (urgent - interview starting)"""
        return await self.send_template_sms(
            to_phone=candidate.get('phone', ''),
            template_name='meeting_link',
            variables={
                'name': candidate.get('name', '').split()[0],
                'company': company_name,
                'link': meeting_link
            },
            candidate_id=candidate.get('id')
        )
    
    async def send_bulk_sms(
        self,
        recipients: List[Dict],
        message: str,
        rate_limit: int = 10
    ) -> Dict:
        """
        Send SMS to multiple recipients with rate limiting
        
        Args:
            recipients: List of dicts with 'phone' and optionally 'name', 'id'
            message: Message to send (can include {name} for personalization)
            rate_limit: Messages per second (Twilio limits apply)
        """
        results = {
            'total': len(recipients),
            'sent': 0,
            'failed': 0,
            'details': []
        }
        
        for i, recipient in enumerate(recipients):
            # Personalize if name available
            personalized = message.replace(
                '{name}',
                recipient.get('name', '').split()[0] or 'there'
            )
            
            result = await self.send_sms(
                to_phone=recipient.get('phone', ''),
                message=personalized,
                candidate_id=recipient.get('id')
            )
            
            if result['status'] == 'success':
                results['sent'] += 1
            else:
                results['failed'] += 1
            
            results['details'].append({
                'phone': recipient.get('phone'),
                'status': result['status'],
                'error': result.get('error')
            })
            
            # Rate limiting
            if (i + 1) % rate_limit == 0:
                await asyncio.sleep(1)
        
        return results
    
    async def get_message_status(self, message_sid: str) -> Dict:
        """Get delivery status of a sent message"""
        if not self.is_configured():
            return {'status': 'error', 'error': 'Twilio not configured'}
        
        try:
            message = await asyncio.to_thread(
                self.client.messages(message_sid).fetch
            )
            
            return {
                'sid': message.sid,
                'status': message.status,  # queued, sending, sent, delivered, failed, undelivered
                'to': message.to,
                'date_sent': str(message.date_sent),
                'error_code': message.error_code,
                'error_message': message.error_message,
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
    
    async def get_incoming_messages(
        self,
        since_date: datetime = None,
        limit: int = 50
    ) -> List[Dict]:
        """
        Get incoming SMS messages (replies from candidates)
        
        Useful for two-way communication
        """
        if not self.is_configured():
            return []
        
        try:
            # Fetch incoming messages
            messages = await asyncio.to_thread(
                lambda: list(self.client.messages.list(
                    to=self.phone_number,
                    date_sent_after=since_date,
                    limit=limit
                ))
            )
            
            return [
                {
                    'sid': msg.sid,
                    'from': msg.from_,
                    'body': msg.body,
                    'date_sent': str(msg.date_sent),
                    'status': msg.status,
                }
                for msg in messages
            ]
        except Exception as e:
            logger.error(f"Error fetching messages: {e}")
            return []
    
    def parse_reply(self, message_body: str) -> Dict:
        """
        Parse candidate SMS reply for common responses
        
        Returns interpreted response
        """
        body = message_body.strip().lower()
        
        # Confirmation responses
        if body in ['yes', 'confirm', 'confirmed', 'ok', 'okay', 'y', '1']:
            return {'intent': 'confirm', 'confidence': 'high'}
        
        if body in ['no', 'cancel', 'n', '0', 'decline']:
            return {'intent': 'decline', 'confidence': 'high'}
        
        if body in ['reschedule', 'postpone', 'later', 'change']:
            return {'intent': 'reschedule', 'confidence': 'high'}
        
        if body in ['stop', 'unsubscribe', 'optout', 'opt out']:
            return {'intent': 'opt_out', 'confidence': 'high'}
        
        if body in ['help', '?', 'info']:
            return {'intent': 'help', 'confidence': 'high'}
        
        # Contains question indicators
        if '?' in body:
            return {'intent': 'question', 'confidence': 'medium', 'message': message_body}
        
        # Unknown - needs human review
        return {'intent': 'unknown', 'confidence': 'low', 'message': message_body}
    
    def get_opt_out_status(self, phone: str) -> bool:
        """Check if phone number has opted out (placeholder)"""
        # In production, check against database of opt-outs
        return False
    
    async def handle_webhook(self, webhook_data: Dict) -> Dict:
        """
        Handle incoming Twilio webhook for message status updates
        
        This would be called from a webhook endpoint
        """
        message_sid = webhook_data.get('MessageSid')
        status = webhook_data.get('MessageStatus')
        from_number = webhook_data.get('From')
        body = webhook_data.get('Body')
        
        if body:
            # Incoming message
            parsed = self.parse_reply(body)
            logger.info(f"📥 SMS from {from_number}: {body} -> {parsed['intent']}")
            
            return {
                'type': 'incoming',
                'from': from_number,
                'body': body,
                'parsed': parsed
            }
        
        if message_sid and status:
            # Status update
            logger.info(f"📱 SMS {message_sid} status: {status}")
            
            return {
                'type': 'status_update',
                'sid': message_sid,
                'status': status
            }
        
        return {'type': 'unknown'}
    
    def get_usage_stats(self) -> Dict:
        """Get SMS usage statistics"""
        today = datetime.now().date()
        
        today_messages = [
            m for m in self.message_log
            if datetime.fromisoformat(m['timestamp']).date() == today
        ]
        
        return {
            'configured': self.is_configured(),
            'from_number': self.phone_number,
            'today_sent': len(today_messages),
            'total_logged': len(self.message_log),
            'templates_available': list(self.TEMPLATES.keys()),
        }


# Singleton
_sms_service = None

def get_sms_service() -> SMSNotificationService:
    global _sms_service
    if _sms_service is None:
        _sms_service = SMSNotificationService()
    return _sms_service

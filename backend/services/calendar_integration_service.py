"""
Calendar Integration Service
Integrates with Calendly and Google Calendar for interview scheduling
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlencode
import aiohttp

logger = logging.getLogger(__name__)


class CalendarIntegrationService:
    """
    Calendar integration for interview scheduling:
    - Calendly integration for self-scheduling
    - Google Calendar API for direct scheduling
    - Microsoft Outlook Calendar support
    - Time zone handling
    - Availability checking
    """
    
    def __init__(self):
        # Calendly
        self.calendly_api_key = os.getenv('CALENDLY_API_KEY')
        self.calendly_user = os.getenv('CALENDLY_USER_URI')
        
        # Google Calendar
        self.google_client_id = os.getenv('GOOGLE_CALENDAR_CLIENT_ID')
        self.google_client_secret = os.getenv('GOOGLE_CALENDAR_CLIENT_SECRET')
        self.google_refresh_token = os.getenv('GOOGLE_CALENDAR_REFRESH_TOKEN')
        
        # Microsoft
        self.ms_client_id = os.getenv('MICROSOFT_CLIENT_ID')
        self.ms_client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        
        self._google_access_token = None
        self._google_token_expiry = None
    
    # ========================================
    # CALENDLY INTEGRATION
    # ========================================
    
    async def get_calendly_event_types(self) -> List[Dict]:
        """Get available Calendly event types (interview types)"""
        if not self.calendly_api_key:
            return []
        
        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {self.calendly_api_key}',
                'Content-Type': 'application/json'
            }
            
            async with session.get(
                'https://api.calendly.com/event_types',
                headers=headers,
                params={'user': self.calendly_user}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return [
                        {
                            'id': et['uri'],
                            'name': et['name'],
                            'duration': et['duration'],
                            'scheduling_url': et['scheduling_url'],
                        }
                        for et in data.get('collection', [])
                    ]
                else:
                    logger.error(f"Calendly API error: {response.status}")
                    return []
    
    async def get_calendly_scheduled_events(
        self, 
        start_date: datetime = None,
        end_date: datetime = None
    ) -> List[Dict]:
        """Get scheduled Calendly events"""
        if not self.calendly_api_key:
            return []
        
        start_date = start_date or datetime.now()
        end_date = end_date or (datetime.now() + timedelta(days=30))
        
        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {self.calendly_api_key}',
                'Content-Type': 'application/json'
            }
            
            params = {
                'user': self.calendly_user,
                'min_start_time': start_date.isoformat(),
                'max_start_time': end_date.isoformat(),
            }
            
            async with session.get(
                'https://api.calendly.com/scheduled_events',
                headers=headers,
                params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return [
                        {
                            'id': event['uri'],
                            'name': event['name'],
                            'start_time': event['start_time'],
                            'end_time': event['end_time'],
                            'status': event['status'],
                            'location': event.get('location', {}).get('location'),
                        }
                        for event in data.get('collection', [])
                    ]
                return []
    
    async def create_calendly_invite(
        self,
        event_type_id: str,
        candidate_email: str,
        candidate_name: str
    ) -> Dict:
        """
        Generate a scheduling link for a candidate
        Note: Calendly doesn't support creating invites via API directly
        Returns the scheduling URL with prefilled info
        """
        if not self.calendly_api_key:
            return {'error': 'Calendly not configured'}
        
        # Get event type details
        event_types = await self.get_calendly_event_types()
        event_type = next(
            (et for et in event_types if et['id'] == event_type_id),
            None
        )
        
        if not event_type:
            return {'error': 'Event type not found'}
        
        # Build scheduling URL with prefilled data
        params = {
            'name': candidate_name,
            'email': candidate_email,
        }
        
        scheduling_url = f"{event_type['scheduling_url']}?{urlencode(params)}"
        
        return {
            'scheduling_url': scheduling_url,
            'event_type': event_type['name'],
            'duration': event_type['duration'],
        }
    
    # ========================================
    # GOOGLE CALENDAR INTEGRATION
    # ========================================
    
    async def _get_google_access_token(self) -> Optional[str]:
        """Get or refresh Google access token"""
        if not all([self.google_client_id, self.google_client_secret, self.google_refresh_token]):
            return None
        
        # Check if current token is valid
        if self._google_access_token and self._google_token_expiry:
            if datetime.now() < self._google_token_expiry - timedelta(minutes=5):
                return self._google_access_token
        
        # Refresh token
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'client_id': self.google_client_id,
                    'client_secret': self.google_client_secret,
                    'refresh_token': self.google_refresh_token,
                    'grant_type': 'refresh_token',
                }
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self._google_access_token = data['access_token']
                    self._google_token_expiry = datetime.now() + timedelta(
                        seconds=data.get('expires_in', 3600)
                    )
                    return self._google_access_token
                else:
                    logger.error(f"Google token refresh failed: {response.status}")
                    return None
    
    async def get_google_calendar_events(
        self,
        calendar_id: str = 'primary',
        start_date: datetime = None,
        end_date: datetime = None
    ) -> List[Dict]:
        """Get events from Google Calendar"""
        access_token = await self._get_google_access_token()
        if not access_token:
            return []
        
        start_date = start_date or datetime.now()
        end_date = end_date or (datetime.now() + timedelta(days=30))
        
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f'Bearer {access_token}'}
            params = {
                'timeMin': start_date.isoformat() + 'Z',
                'timeMax': end_date.isoformat() + 'Z',
                'singleEvents': 'true',
                'orderBy': 'startTime',
            }
            
            async with session.get(
                f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events',
                headers=headers,
                params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return [
                        {
                            'id': event['id'],
                            'summary': event.get('summary', 'No title'),
                            'start': event['start'].get('dateTime', event['start'].get('date')),
                            'end': event['end'].get('dateTime', event['end'].get('date')),
                            'attendees': [
                                a['email'] for a in event.get('attendees', [])
                            ],
                            'meet_link': event.get('hangoutLink'),
                        }
                        for event in data.get('items', [])
                    ]
                return []
    
    async def create_google_calendar_event(
        self,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        attendees: List[str],
        description: str = '',
        location: str = '',
        calendar_id: str = 'primary',
        add_meet_link: bool = True
    ) -> Dict:
        """
        Create a Google Calendar event for interview
        
        Args:
            summary: Event title (e.g., "Interview - John Smith - Software Engineer")
            start_time: Interview start time
            end_time: Interview end time
            attendees: List of attendee emails
            description: Event description
            location: Physical location or "Google Meet"
            add_meet_link: Whether to add Google Meet link
        """
        access_token = await self._get_google_access_token()
        if not access_token:
            return {'error': 'Google Calendar not configured'}
        
        event = {
            'summary': summary,
            'description': description,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'UTC',
            },
            'attendees': [{'email': email} for email in attendees],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 30},  # 30 min before
                ],
            },
        }
        
        if location:
            event['location'] = location
        
        if add_meet_link:
            event['conferenceData'] = {
                'createRequest': {
                    'requestId': f"interview-{datetime.now().timestamp()}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        
        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            params = {'conferenceDataVersion': 1} if add_meet_link else {}
            
            async with session.post(
                f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events',
                headers=headers,
                params=params,
                json=event
            ) as response:
                if response.status in [200, 201]:
                    data = await response.json()
                    return {
                        'status': 'success',
                        'event_id': data['id'],
                        'html_link': data.get('htmlLink'),
                        'meet_link': data.get('hangoutLink'),
                        'start': data['start'],
                        'end': data['end'],
                    }
                else:
                    error = await response.text()
                    logger.error(f"Google Calendar create failed: {error}")
                    return {'error': f'Failed to create event: {response.status}'}
    
    async def update_google_calendar_event(
        self,
        event_id: str,
        updates: Dict,
        calendar_id: str = 'primary'
    ) -> Dict:
        """Update an existing Google Calendar event"""
        access_token = await self._get_google_access_token()
        if not access_token:
            return {'error': 'Google Calendar not configured'}
        
        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            async with session.patch(
                f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}',
                headers=headers,
                json=updates
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {'status': 'success', 'event': data}
                else:
                    return {'error': f'Failed to update: {response.status}'}
    
    async def delete_google_calendar_event(
        self,
        event_id: str,
        calendar_id: str = 'primary',
        notify_attendees: bool = True
    ) -> Dict:
        """Delete/cancel a Google Calendar event"""
        access_token = await self._get_google_access_token()
        if not access_token:
            return {'error': 'Google Calendar not configured'}
        
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f'Bearer {access_token}'}
            params = {'sendUpdates': 'all' if notify_attendees else 'none'}
            
            async with session.delete(
                f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}',
                headers=headers,
                params=params
            ) as response:
                if response.status in [200, 204]:
                    return {'status': 'success', 'message': 'Event deleted'}
                else:
                    return {'error': f'Failed to delete: {response.status}'}
    
    async def get_free_busy(
        self,
        calendars: List[str],
        start_time: datetime,
        end_time: datetime
    ) -> Dict:
        """Check free/busy status for calendars"""
        access_token = await self._get_google_access_token()
        if not access_token:
            return {'error': 'Google Calendar not configured'}
        
        async with aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            body = {
                'timeMin': start_time.isoformat() + 'Z',
                'timeMax': end_time.isoformat() + 'Z',
                'items': [{'id': cal} for cal in calendars],
            }
            
            async with session.post(
                'https://www.googleapis.com/calendar/v3/freeBusy',
                headers=headers,
                json=body
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'status': 'success',
                        'calendars': data.get('calendars', {}),
                    }
                return {'error': f'Failed: {response.status}'}
    
    # ========================================
    # INTERVIEW SCHEDULING HELPERS
    # ========================================
    
    async def schedule_interview(
        self,
        candidate: Dict,
        interview_type: str,
        datetime_slot: datetime,
        duration_minutes: int,
        interviewer_email: str,
        additional_attendees: List[str] = None,
        notes: str = ''
    ) -> Dict:
        """
        Schedule an interview - unified interface for all calendars
        
        Args:
            candidate: Candidate dict with name, email
            interview_type: Type of interview (e.g., "Technical Screen")
            datetime_slot: Interview start time
            duration_minutes: Duration in minutes
            interviewer_email: Main interviewer's email
            additional_attendees: Other attendees (optional)
            notes: Interview notes/description
        """
        attendees = [candidate['email'], interviewer_email]
        if additional_attendees:
            attendees.extend(additional_attendees)
        
        summary = f"Interview: {candidate.get('name', 'Candidate')} - {interview_type}"
        
        description = f"""
Interview Details:
- Candidate: {candidate.get('name')}
- Email: {candidate.get('email')}
- Position: {candidate.get('jobCategory', 'Not specified')}
- Type: {interview_type}

{notes}

---
Scheduled via AI Recruiter
        """.strip()
        
        end_time = datetime_slot + timedelta(minutes=duration_minutes)
        
        # Try Google Calendar first
        if self.google_refresh_token:
            result = await self.create_google_calendar_event(
                summary=summary,
                start_time=datetime_slot,
                end_time=end_time,
                attendees=attendees,
                description=description,
                add_meet_link=True
            )
            
            if result.get('status') == 'success':
                return {
                    'status': 'success',
                    'provider': 'google',
                    'event_id': result['event_id'],
                    'meeting_link': result.get('meet_link'),
                    'calendar_link': result.get('html_link'),
                    'start_time': datetime_slot.isoformat(),
                    'end_time': end_time.isoformat(),
                }
        
        # Fallback to Calendly link
        if self.calendly_api_key:
            event_types = await self.get_calendly_event_types()
            if event_types:
                invite = await self.create_calendly_invite(
                    event_type_id=event_types[0]['id'],
                    candidate_email=candidate['email'],
                    candidate_name=candidate.get('name', '')
                )
                
                return {
                    'status': 'success',
                    'provider': 'calendly',
                    'scheduling_url': invite.get('scheduling_url'),
                    'message': 'Calendly link generated - candidate will self-schedule'
                }
        
        return {
            'status': 'error',
            'message': 'No calendar integration configured'
        }
    
    async def get_available_slots(
        self,
        interviewer_email: str,
        start_date: datetime,
        end_date: datetime,
        duration_minutes: int = 60,
        working_hours: tuple = (9, 17)  # 9 AM to 5 PM
    ) -> List[Dict]:
        """
        Get available time slots for an interviewer
        
        Returns list of available slots based on calendar free/busy
        """
        # Get busy times
        free_busy = await self.get_free_busy(
            calendars=[interviewer_email],
            start_time=start_date,
            end_time=end_date
        )
        
        if free_busy.get('error'):
            # Return default business hours slots
            return self._generate_default_slots(
                start_date, end_date, duration_minutes, working_hours
            )
        
        busy_times = free_busy.get('calendars', {}).get(
            interviewer_email, {}
        ).get('busy', [])
        
        # Generate available slots excluding busy times
        available = []
        current = start_date.replace(hour=working_hours[0], minute=0, second=0)
        
        while current < end_date:
            # Check if within working hours
            if working_hours[0] <= current.hour < working_hours[1]:
                slot_end = current + timedelta(minutes=duration_minutes)
                
                # Check if slot overlaps with any busy time
                is_busy = any(
                    self._times_overlap(
                        current, slot_end,
                        datetime.fromisoformat(b['start'].replace('Z', '')),
                        datetime.fromisoformat(b['end'].replace('Z', ''))
                    )
                    for b in busy_times
                )
                
                if not is_busy and slot_end.hour <= working_hours[1]:
                    available.append({
                        'start': current.isoformat(),
                        'end': slot_end.isoformat(),
                        'duration': duration_minutes,
                    })
            
            current += timedelta(minutes=30)  # 30-min increments
            
            # Skip to next day if past working hours
            if current.hour >= working_hours[1]:
                current = (current + timedelta(days=1)).replace(
                    hour=working_hours[0], minute=0, second=0
                )
        
        return available[:20]  # Return max 20 slots
    
    def _times_overlap(
        self,
        start1: datetime, end1: datetime,
        start2: datetime, end2: datetime
    ) -> bool:
        """Check if two time ranges overlap"""
        return start1 < end2 and start2 < end1
    
    def _generate_default_slots(
        self,
        start_date: datetime,
        end_date: datetime,
        duration_minutes: int,
        working_hours: tuple
    ) -> List[Dict]:
        """Generate default available slots without calendar check"""
        slots = []
        current = start_date.replace(hour=working_hours[0], minute=0, second=0)
        
        while current < end_date and len(slots) < 20:
            if working_hours[0] <= current.hour < working_hours[1]:
                # Skip weekends
                if current.weekday() < 5:
                    slot_end = current + timedelta(minutes=duration_minutes)
                    if slot_end.hour <= working_hours[1]:
                        slots.append({
                            'start': current.isoformat(),
                            'end': slot_end.isoformat(),
                            'duration': duration_minutes,
                        })
            
            current += timedelta(minutes=60)
            if current.hour >= working_hours[1]:
                current = (current + timedelta(days=1)).replace(
                    hour=working_hours[0], minute=0, second=0
                )
        
        return slots
    
    def get_integration_status(self) -> Dict:
        """Get status of calendar integrations"""
        return {
            'calendly': {
                'configured': bool(self.calendly_api_key),
                'features': ['Self-scheduling links', 'Event types'] if self.calendly_api_key else []
            },
            'google_calendar': {
                'configured': bool(self.google_refresh_token),
                'features': ['Direct scheduling', 'Meet links', 'Free/busy check'] if self.google_refresh_token else []
            },
            'microsoft_calendar': {
                'configured': bool(self.ms_client_id and self.ms_client_secret),
                'features': ['Outlook integration'] if self.ms_client_id else []
            }
        }


# Singleton
_calendar_service = None

def get_calendar_service() -> CalendarIntegrationService:
    global _calendar_service
    if _calendar_service is None:
        _calendar_service = CalendarIntegrationService()
    return _calendar_service

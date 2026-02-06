from typing import Dict, Any, Optional
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
import base64
import logging

logger = logging.getLogger(__name__)

class MicrosoftGraphService:
    """
    Microsoft Graph API integration for Outlook/Office 365
    Provides enterprise-grade email access with OAuth2
    Supports both delegated permissions (user flow) and application permissions (service principal)
    """
    
    def __init__(self, client_id: str, client_secret: str, tenant_id: str, user_email: str = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.user_email = user_email  # Required for application permissions
        self.graph_url = "https://graph.microsoft.com/v1.0"
        self.access_token = None
        self.token_expiry = None
        self.auth_type = None  # 'delegated' or 'application'
    
    async def authenticate(self, authorization_code: str, redirect_uri: str) -> Dict[str, Any]:
        """
        Authenticate using OAuth2 authorization code flow (delegated permissions)
        """
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': authorization_code,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
            'scope': 'https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access'
        }
        
        try:
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.auth_type = 'delegated'
            
            # Calculate token expiry
            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
            
            return {
                'status': 'success',
                'access_token': self.access_token,
                'expires_in': expires_in
            }
        
        except Exception as e:
            return {
                'status': 'failed',
                'error': str(e)
            }
    
    def authenticate_with_credentials(self) -> Dict[str, Any]:
        """
        Authenticate using OAuth2 client credentials flow (application permissions)
        This allows server-to-server authentication without user interaction
        Requires application permission: Mail.Read on the Azure app registration
        """
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'scope': 'https://graph.microsoft.com/.default',
            'grant_type': 'client_credentials'
        }
        
        try:
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.auth_type = 'application'  # Mark as application permissions
            
            # Calculate token expiry
            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
            
            return {
                'status': 'success',
                'access_token': self.access_token,
                'expires_in': expires_in,
                'token_type': token_data.get('token_type', 'Bearer')
            }
        
        except Exception as e:
            return {
                'status': 'failed',
                'error': str(e)
            }
    
    async def get_messages(
        self,
        folder: str = 'inbox',
        filter_query: Optional[str] = None,
        top: int = 50,
        fetch_all: bool = False
    ) -> Dict[str, Any]:
        """
        Get messages from specified folder
        If fetch_all=True, uses pagination to get ALL messages (no limit)
        Handles both delegated (user) and application (service) permissions
        """
        if not self._is_token_valid():
            return {'status': 'error', 'message': 'Token expired or invalid'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        all_messages = []
        
        # Build URL based on auth type
        # For delegated permissions: use /me/mailFolders
        # For application permissions: use /users/{email}/mailFolders
        if self.auth_type == 'application' and self.user_email:
            # Application permissions: must specify user email explicitly
            url = f"{self.graph_url}/users/{self.user_email}/mailFolders/{folder}/messages"
        else:
            # Delegated permissions: use /me
            url = f"{self.graph_url}/me/mailFolders/{folder}/messages"
        
        # Always fetch max page size (999) for efficiency
        page_size = 999
        params = {'$top': page_size, '$orderby': 'receivedDateTime desc'}
        
        if filter_query:
            params['$filter'] = filter_query
        
        try:
            page_count = 0
            while url:
                page_count += 1
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                messages = data.get('value', [])
                all_messages.extend(messages)
                
                logger.info(f"📧 Fetched page {page_count}: {len(messages)} emails (total: {len(all_messages)})")
                
                # If NOT fetching all, stop after reaching 'top' messages
                if not fetch_all and len(all_messages) >= top:
                    break
                
                # Get next page URL if available
                url = data.get('@odata.nextLink')
                params = {}  # nextLink already contains all parameters
                
                # Safety: if fetch_all but no more pages, we're done
                if not url:
                    break
            
            # Trim to requested amount if not fetching all
            if not fetch_all and len(all_messages) > top:
                all_messages = all_messages[:top]
            
            logger.info(f"📧 Total emails fetched: {len(all_messages)} from {page_count} pages")
            
            return {
                'status': 'success',
                'messages': all_messages,
                'count': len(all_messages)
            }
        
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
    
    async def get_message_with_attachments(self, message_id: str) -> Dict[str, Any]:
        """
        Get full message with attachments
        """
        if not self._is_token_valid():
            return {'status': 'error', 'message': 'Token expired'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            # Use correct endpoint based on auth type
            if self.auth_type == 'application' and self.user_email:
                base_url = f"{self.graph_url}/users/{self.user_email}/messages/{message_id}"
            else:
                base_url = f"{self.graph_url}/me/messages/{message_id}"
            
            # Get message
            message_response = requests.get(base_url, headers=headers)
            message_response.raise_for_status()
            message_data = message_response.json()
            
            # Get attachments
            attachments_url = f"{base_url}/attachments"
            attachments_response = requests.get(attachments_url, headers=headers)
            attachments_response.raise_for_status()
            attachments_data = attachments_response.json()
            
            # Process attachments - extract file data
            processed_attachments = []
            for att in attachments_data.get('value', []):
                if att.get('@odata.type') == '#microsoft.graph.fileAttachment':
                    # Decode base64 content
                    import base64
                    file_content = base64.b64decode(att.get('contentBytes', ''))
                    processed_attachments.append({
                        'filename': att.get('name', 'unknown'),
                        'data': file_content,
                        'content_type': att.get('contentType', 'application/octet-stream'),
                        'size': att.get('size', 0)
                    })
            
            return {
                'status': 'success',
                'message': message_data,
                'attachments': processed_attachments
            }
        
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
    
    async def search_application_emails(self, keywords: list = None) -> Dict[str, Any]:
        """
        Search for job application emails
        """
        if keywords is None:
            keywords = ['application', 'resume', 'cv', 'applying', 'position']
        
        # Build search query
        search_terms = ' OR '.join([f'"{keyword}"' for keyword in keywords])
        filter_query = f"contains(subject, '{keywords[0]}')"
        
        return await self.get_messages(filter_query=filter_query)
    
    async def parse_email_for_candidate(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Outlook email and extract candidate information
        """
        candidate_info = {
            'name': message_data.get('from', {}).get('emailAddress', {}).get('name', 'Unknown'),
            'email': message_data.get('from', {}).get('emailAddress', {}).get('address', ''),
            'subject': message_data.get('subject', ''),
            'received_date': message_data.get('receivedDateTime', ''),
            'body': message_data.get('body', {}).get('content', ''),
            'body_preview': message_data.get('bodyPreview', ''),
            'has_attachments': message_data.get('hasAttachments', False),
            'importance': message_data.get('importance', 'normal')
        }
        
        return candidate_info
    
    async def download_attachment(self, message_id: str, attachment_id: str) -> Dict[str, Any]:
        """
        Download email attachment (resume)
        """
        if not self._is_token_valid():
            return {'status': 'error', 'message': 'Token expired'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}'
        }
        
        try:
            url = f"{self.graph_url}/me/messages/{message_id}/attachments/{attachment_id}"
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            attachment_data = response.json()
            
            return {
                'status': 'success',
                'filename': attachment_data.get('name', ''),
                'content_type': attachment_data.get('contentType', ''),
                'size': attachment_data.get('size', 0),
                'content': attachment_data.get('contentBytes', '')
            }
        
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
    
    async def create_folder(self, folder_name: str, parent_folder: str = 'inbox') -> Dict[str, Any]:
        """
        Create a folder for organizing applications
        """
        if not self._is_token_valid():
            return {'status': 'error', 'message': 'Token expired'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        data = {'displayName': folder_name}
        
        try:
            url = f"{self.graph_url}/me/mailFolders/{parent_folder}/childFolders"
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            folder_data = response.json()
            
            return {
                'status': 'success',
                'folder_id': folder_data.get('id'),
                'folder_name': folder_data.get('displayName')
            }
        
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
    
    async def move_message(self, message_id: str, destination_folder_id: str) -> Dict[str, Any]:
        """
        Move message to a folder (e.g., "Processed Applications")
        """
        if not self._is_token_valid():
            return {'status': 'error', 'message': 'Token expired'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        data = {'destinationId': destination_folder_id}
        
        try:
            url = f"{self.graph_url}/me/messages/{message_id}/move"
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            return {'status': 'success', 'message': 'Message moved successfully'}
        
        except Exception as e:
            return {'status': 'error', 'message': str(e)}
    
    def _is_token_valid(self) -> bool:
        """Check if access token is still valid"""
        if not self.access_token or not self.token_expiry:
            return False
        
        return datetime.now() < self.token_expiry
    
    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh an expired access token using the refresh token
        This allows continued access without user re-authentication
        """
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'scope': 'https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access'
        }
        
        try:
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.auth_type = 'delegated'
            
            # Calculate token expiry
            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info("✅ Successfully refreshed OAuth2 access token")
            
            return {
                'status': 'success',
                'access_token': self.access_token,
                'refresh_token': token_data.get('refresh_token', refresh_token),  # New refresh token if provided
                'expires_in': expires_in
            }
        
        except Exception as e:
            logger.error(f"Failed to refresh token: {str(e)}")
            return {
                'status': 'failed',
                'error': str(e)
            }
    
    def get_authorization_url(self, redirect_uri: str, state: str = None) -> str:
        """
        Get OAuth2 authorization URL for user consent
        """
        scopes = [
            'https://graph.microsoft.com/Mail.Read',
            'https://graph.microsoft.com/Mail.ReadWrite',
            'https://graph.microsoft.com/User.Read',
            'offline_access'  # Required for refresh tokens
        ]
        
        scope_string = ' '.join(scopes)
        
        # URL encode the redirect_uri and scope
        encoded_redirect = quote(redirect_uri, safe='')
        encoded_scope = quote(scope_string, safe='')
        
        auth_url = (
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?"
            f"client_id={self.client_id}"
            f"&response_type=code"
            f"&redirect_uri={encoded_redirect}"
            f"&scope={encoded_scope}"
            f"&response_mode=query"
        )
        
        if state:
            auth_url += f"&state={state}"
        
        return auth_url

"""
OAuth2 Automation Service
Automatic token refresh and email sync with zero user intervention
Falls back to manual sync only when absolutely necessary
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List
from enum import Enum
import json
import os

logger = logging.getLogger(__name__)


class AuthStatus(Enum):
    """Authentication status"""
    VALID = "valid"
    EXPIRED = "expired"
    REFRESHING = "refreshing"
    NEEDS_REAUTH = "needs_reauth"
    NO_TOKEN = "no_token"
    ERROR = "error"


class SyncStatus(Enum):
    """Email sync status"""
    IDLE = "idle"
    SYNCING = "syncing"
    SUCCESS = "success"
    FAILED = "failed"
    WAITING_AUTH = "waiting_auth"


class OAuthAutomationService:
    """
    Automated OAuth2 token management and email sync
    
    Features:
    - Automatic token refresh before expiry
    - Client credentials fallback
    - Scheduled sync with retry logic
    - Health monitoring
    - Event callbacks for UI updates
    """
    
    def __init__(
        self,
        client_id: str = None,
        client_secret: str = None,
        tenant_id: str = None,
        primary_email: str = None,
        token_storage = None,
        graph_service_factory = None
    ):
        self.client_id = client_id or os.getenv('MICROSOFT_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('MICROSOFT_CLIENT_SECRET')
        self.tenant_id = tenant_id or os.getenv('MICROSOFT_TENANT_ID')
        self.primary_email = primary_email or os.getenv('EMAIL_ADDRESS')
        
        self._token_storage = token_storage
        self._graph_service_factory = graph_service_factory
        
        # State
        self._auth_status = AuthStatus.NO_TOKEN
        self._sync_status = SyncStatus.IDLE
        self._last_sync_time: Optional[datetime] = None
        self._last_sync_result: Optional[Dict] = None
        self._next_sync_time: Optional[datetime] = None
        self._sync_interval_minutes = int(os.getenv('SYNC_INTERVAL_MINUTES', '15'))
        self._token_refresh_margin_minutes = 10  # Refresh 10 minutes before expiry
        
        # Retry configuration
        self._max_retries = 3
        self._retry_delay_base = 30  # seconds
        self._current_retry = 0
        
        # Background tasks
        self._sync_task: Optional[asyncio.Task] = None
        self._token_monitor_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Event callbacks
        self._on_auth_status_change: List[Callable] = []
        self._on_sync_status_change: List[Callable] = []
        self._on_sync_complete: List[Callable] = []
        
        # Statistics
        self._stats = {
            'total_syncs': 0,
            'successful_syncs': 0,
            'failed_syncs': 0,
            'token_refreshes': 0,
            'auto_reauths': 0,
            'emails_processed': 0,
            'candidates_added': 0
        }
        
        logger.info("🔐 OAuth Automation Service initialized")
    
    def register_callback(self, event: str, callback: Callable):
        """Register event callback"""
        if event == 'auth_status':
            self._on_auth_status_change.append(callback)
        elif event == 'sync_status':
            self._on_sync_status_change.append(callback)
        elif event == 'sync_complete':
            self._on_sync_complete.append(callback)
    
    def _emit_event(self, event: str, data: Any = None):
        """Emit event to registered callbacks"""
        callbacks = []
        if event == 'auth_status':
            callbacks = self._on_auth_status_change
        elif event == 'sync_status':
            callbacks = self._on_sync_status_change
        elif event == 'sync_complete':
            callbacks = self._on_sync_complete
        
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(data))
                else:
                    callback(data)
            except Exception as e:
                logger.warning(f"Event callback error: {e}")
    
    @property
    def is_configured(self) -> bool:
        """Check if OAuth2 is properly configured"""
        return all([self.client_id, self.client_secret, self.tenant_id, self.primary_email])
    
    @property
    def auth_status(self) -> AuthStatus:
        return self._auth_status
    
    @property
    def sync_status(self) -> SyncStatus:
        return self._sync_status
    
    @property
    def stats(self) -> Dict:
        return {
            **self._stats,
            'auth_status': self._auth_status.value,
            'sync_status': self._sync_status.value,
            'last_sync': self._last_sync_time.isoformat() if self._last_sync_time else None,
            'next_sync': self._next_sync_time.isoformat() if self._next_sync_time else None,
            'sync_interval_minutes': self._sync_interval_minutes
        }
    
    def _set_auth_status(self, status: AuthStatus):
        """Update auth status and emit event"""
        if self._auth_status != status:
            old_status = self._auth_status
            self._auth_status = status
            logger.info(f"🔐 Auth status: {old_status.value} → {status.value}")
            self._emit_event('auth_status', {'old': old_status.value, 'new': status.value})
    
    def _set_sync_status(self, status: SyncStatus):
        """Update sync status and emit event"""
        if self._sync_status != status:
            self._sync_status = status
            self._emit_event('sync_status', status.value)
    
    async def check_auth_status(self) -> AuthStatus:
        """Check current authentication status"""
        if not self.is_configured:
            self._set_auth_status(AuthStatus.NO_TOKEN)
            return AuthStatus.NO_TOKEN
        
        try:
            token_data = self._token_storage.get_token(self.primary_email)
            
            if not token_data:
                self._set_auth_status(AuthStatus.NO_TOKEN)
                return AuthStatus.NO_TOKEN
            
            is_expired = token_data.get('is_expired', True)
            has_refresh = bool(token_data.get('refresh_token'))
            
            if not is_expired:
                # Check if approaching expiry
                expires_at = token_data.get('expires_at_dt')
                if expires_at:
                    time_to_expiry = (expires_at - datetime.now()).total_seconds() / 60
                    if time_to_expiry < self._token_refresh_margin_minutes:
                        # Token about to expire - refresh proactively
                        if has_refresh:
                            self._set_auth_status(AuthStatus.EXPIRED)
                            return AuthStatus.EXPIRED
                
                self._set_auth_status(AuthStatus.VALID)
                return AuthStatus.VALID
            
            if has_refresh:
                self._set_auth_status(AuthStatus.EXPIRED)
                return AuthStatus.EXPIRED
            
            self._set_auth_status(AuthStatus.NEEDS_REAUTH)
            return AuthStatus.NEEDS_REAUTH
            
        except Exception as e:
            logger.error(f"Error checking auth status: {e}")
            self._set_auth_status(AuthStatus.ERROR)
            return AuthStatus.ERROR
    
    async def ensure_valid_token(self) -> Dict[str, Any]:
        """
        Ensure we have a valid access token
        Automatically refreshes if expired
        Returns token data or error
        """
        if not self.is_configured:
            return {'status': 'error', 'message': 'OAuth2 not configured'}
        
        status = await self.check_auth_status()
        
        if status == AuthStatus.VALID:
            token_data = self._token_storage.get_token(self.primary_email)
            return {'status': 'success', 'token': token_data}
        
        if status == AuthStatus.EXPIRED:
            # Try to refresh the token
            return await self.refresh_token()
        
        if status == AuthStatus.NO_TOKEN:
            # Try client credentials as fallback
            return await self.authenticate_with_credentials()
        
        return {'status': 'error', 'message': f'Auth status: {status.value}'}
    
    async def refresh_token(self) -> Dict[str, Any]:
        """Refresh the OAuth2 access token"""
        self._set_auth_status(AuthStatus.REFRESHING)
        
        try:
            token_data = self._token_storage.get_token(self.primary_email)
            if not token_data or not token_data.get('refresh_token'):
                # No refresh token - try client credentials
                logger.info("No refresh token available, trying client credentials...")
                return await self.authenticate_with_credentials()
            
            refresh_token = token_data['refresh_token']
            
            # Create graph service and refresh
            graph_service = self._graph_service_factory(
                self.client_id, self.client_secret, self.tenant_id, self.primary_email
            )
            
            result = await graph_service.refresh_access_token(refresh_token)
            
            if result['status'] == 'success':
                # Save new tokens
                self._token_storage.save_token(
                    email=self.primary_email,
                    access_token=result['access_token'],
                    refresh_token=result.get('refresh_token', refresh_token),
                    expires_in=result['expires_in'],
                    auth_type='delegated'
                )
                
                self._stats['token_refreshes'] += 1
                self._set_auth_status(AuthStatus.VALID)
                logger.info(f"✅ Token refreshed successfully for {self.primary_email}")
                
                return {
                    'status': 'success',
                    'token': self._token_storage.get_token(self.primary_email)
                }
            else:
                logger.warning(f"Token refresh failed: {result.get('error')}")
                # Try client credentials as fallback
                return await self.authenticate_with_credentials()
                
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            self._set_auth_status(AuthStatus.ERROR)
            return {'status': 'error', 'message': str(e)}
    
    async def authenticate_with_credentials(self) -> Dict[str, Any]:
        """
        Authenticate using client credentials (application permissions)
        This is a fallback when refresh token fails
        """
        try:
            graph_service = self._graph_service_factory(
                self.client_id, self.client_secret, self.tenant_id, self.primary_email
            )
            
            result = await graph_service.authenticate_with_credentials()
            
            if result['status'] == 'success':
                # Save token (no refresh token for client credentials)
                self._token_storage.save_token(
                    email=self.primary_email,
                    access_token=result['access_token'],
                    refresh_token=None,
                    expires_in=result['expires_in'],
                    auth_type='application'
                )
                
                self._stats['auto_reauths'] += 1
                self._set_auth_status(AuthStatus.VALID)
                logger.info(f"✅ Client credentials auth successful for {self.primary_email}")
                
                return {
                    'status': 'success',
                    'token': self._token_storage.get_token(self.primary_email),
                    'auth_type': 'application'
                }
            else:
                self._set_auth_status(AuthStatus.NEEDS_REAUTH)
                return {
                    'status': 'error',
                    'message': result.get('error', 'Client credentials auth failed'),
                    'needs_manual_auth': True
                }
                
        except Exception as e:
            logger.error(f"Client credentials auth error: {e}")
            self._set_auth_status(AuthStatus.NEEDS_REAUTH)
            return {
                'status': 'error',
                'message': str(e),
                'needs_manual_auth': True
            }
    
    async def start(self):
        """Start automated sync and token monitoring"""
        if self._running:
            return
        
        self._running = True
        
        # Start token monitor
        self._token_monitor_task = asyncio.create_task(self._token_monitor_loop())
        
        # Start sync scheduler
        self._sync_task = asyncio.create_task(self._sync_scheduler_loop())
        
        logger.info("🚀 OAuth Automation Service started")
    
    async def stop(self):
        """Stop automated sync"""
        self._running = False
        
        if self._token_monitor_task:
            self._token_monitor_task.cancel()
            try:
                await self._token_monitor_task
            except asyncio.CancelledError:
                pass
        
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 OAuth Automation Service stopped")
    
    async def _token_monitor_loop(self):
        """Monitor token status and auto-refresh before expiry"""
        while self._running:
            try:
                status = await self.check_auth_status()
                
                if status == AuthStatus.EXPIRED:
                    logger.info("🔄 Token expiring soon, auto-refreshing...")
                    await self.ensure_valid_token()
                
                # Check every 5 minutes
                await asyncio.sleep(300)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Token monitor error: {e}")
                await asyncio.sleep(60)
    
    async def _sync_scheduler_loop(self):
        """Scheduled email sync loop with retry logic"""
        # Initial delay to let server start
        await asyncio.sleep(10)
        
        while self._running:
            try:
                # Calculate next sync time
                self._next_sync_time = datetime.now() + timedelta(minutes=self._sync_interval_minutes)
                
                # Perform sync
                await self.perform_sync()
                
                # Reset retry counter on success
                self._current_retry = 0
                
                # Wait for next sync interval
                await asyncio.sleep(self._sync_interval_minutes * 60)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync scheduler error: {e}")
                self._current_retry += 1
                
                if self._current_retry < self._max_retries:
                    retry_delay = self._retry_delay_base * (2 ** self._current_retry)
                    logger.info(f"⏳ Retrying sync in {retry_delay}s (attempt {self._current_retry + 1}/{self._max_retries})")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("❌ Max sync retries reached, waiting for next interval")
                    self._current_retry = 0
                    await asyncio.sleep(self._sync_interval_minutes * 60)
    
    async def perform_sync(self, sync_callback: Callable = None) -> Dict[str, Any]:
        """
        Perform email sync with automatic token management
        
        Args:
            sync_callback: Optional callback for actual sync logic
        
        Returns:
            Sync result dictionary
        """
        self._set_sync_status(SyncStatus.SYNCING)
        self._stats['total_syncs'] += 1
        
        try:
            # Ensure valid token first
            token_result = await self.ensure_valid_token()
            
            if token_result['status'] != 'success':
                if token_result.get('needs_manual_auth'):
                    self._set_sync_status(SyncStatus.WAITING_AUTH)
                    return {
                        'status': 'waiting_auth',
                        'message': 'Manual authentication required',
                        'auth_url': self._get_auth_url()
                    }
                else:
                    self._set_sync_status(SyncStatus.FAILED)
                    self._stats['failed_syncs'] += 1
                    return token_result
            
            # Token is valid - perform sync
            if sync_callback:
                result = await sync_callback(token_result['token'])
            else:
                result = {
                    'status': 'success',
                    'message': 'Token validated, sync ready',
                    'token': token_result['token']
                }
            
            self._last_sync_time = datetime.now()
            self._last_sync_result = result
            
            if result.get('status') == 'success':
                self._set_sync_status(SyncStatus.SUCCESS)
                self._stats['successful_syncs'] += 1
                if 'emails_processed' in result:
                    self._stats['emails_processed'] += result['emails_processed']
                if 'candidates_added' in result:
                    self._stats['candidates_added'] += result['candidates_added']
            else:
                self._set_sync_status(SyncStatus.FAILED)
                self._stats['failed_syncs'] += 1
            
            self._emit_event('sync_complete', result)
            return result
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            self._set_sync_status(SyncStatus.FAILED)
            self._stats['failed_syncs'] += 1
            return {'status': 'error', 'message': str(e)}
        finally:
            # Return to idle after a short delay
            await asyncio.sleep(2)
            if self._sync_status in [SyncStatus.SUCCESS, SyncStatus.FAILED]:
                self._set_sync_status(SyncStatus.IDLE)
    
    async def trigger_manual_sync(self, sync_callback: Callable = None) -> Dict[str, Any]:
        """Trigger manual sync (emergency fallback)"""
        logger.info("🔧 Manual sync triggered")
        return await self.perform_sync(sync_callback)
    
    def _get_auth_url(self) -> Optional[str]:
        """Get OAuth2 authorization URL for manual auth"""
        if not self.is_configured:
            return None
        
        try:
            graph_service = self._graph_service_factory(
                self.client_id, self.client_secret, self.tenant_id, self.primary_email
            )
            return graph_service.get_authorization_url(
                redirect_uri='http://localhost:5173/email',
                state=self.primary_email
            )
        except Exception as e:
            logger.error(f"Error generating auth URL: {e}")
            return None
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive status summary for frontend"""
        return {
            'is_configured': self.is_configured,
            'auth_status': self._auth_status.value,
            'sync_status': self._sync_status.value,
            'primary_email': self.primary_email,
            'last_sync': self._last_sync_time.isoformat() if self._last_sync_time else None,
            'next_sync': self._next_sync_time.isoformat() if self._next_sync_time else None,
            'sync_interval_minutes': self._sync_interval_minutes,
            'stats': self._stats,
            'needs_manual_auth': self._auth_status == AuthStatus.NEEDS_REAUTH,
            'auth_url': self._get_auth_url() if self._auth_status == AuthStatus.NEEDS_REAUTH else None
        }


# Global instance
_oauth_automation: Optional[OAuthAutomationService] = None


def get_oauth_automation(
    token_storage = None,
    graph_service_factory = None
) -> OAuthAutomationService:
    """Get global OAuth automation service instance"""
    global _oauth_automation
    
    if _oauth_automation is None:
        from services.token_storage import get_token_storage
        from services.microsoft_graph import MicrosoftGraphService
        
        _oauth_automation = OAuthAutomationService(
            token_storage=token_storage or get_token_storage(),
            graph_service_factory=graph_service_factory or MicrosoftGraphService
        )
    
    return _oauth_automation


async def init_oauth_automation():
    """Initialize and start OAuth automation service"""
    service = get_oauth_automation()
    await service.start()
    return service

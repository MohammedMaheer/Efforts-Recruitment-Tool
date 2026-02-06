"""
OAuth2 Token Storage Service
Stores and manages access tokens for email accounts
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

class TokenStorage:
    """Store OAuth2 tokens in a JSON file"""
    
    def __init__(self, storage_file: str = "oauth_tokens.json"):
        self.storage_file = storage_file
        self._ensure_storage_exists()
    
    def _ensure_storage_exists(self):
        """Create storage file if it doesn't exist"""
        if not os.path.exists(self.storage_file):
            with open(self.storage_file, 'w') as f:
                json.dump({}, f)
    
    def save_token(self, email: str, access_token: str, refresh_token: Optional[str], expires_in: int, auth_type: str = 'delegated'):
        """Save OAuth2 token for an email account"""
        try:
            tokens = self._load_tokens()
            
            expiry_time = datetime.now() + timedelta(seconds=expires_in)
            
            tokens[email] = {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': expiry_time.isoformat(),
                'updated_at': datetime.now().isoformat(),
                'auth_type': auth_type  # 'delegated' for user login, 'application' for client credentials
            }
            
            with open(self.storage_file, 'w') as f:
                json.dump(tokens, f, indent=2)
            
            logger.info(f"✅ Saved OAuth2 token for {email}")
            return True
        except Exception as e:
            logger.error(f"Error saving token: {str(e)}")
            return False
    
    def get_token(self, email: str) -> Optional[Dict]:
        """Get OAuth2 token for an email account, with expiry status"""
        try:
            tokens = self._load_tokens()
            token_data = tokens.get(email)
            
            if not token_data:
                return None
            
            # Check if token is expired
            expires_at = datetime.fromisoformat(token_data['expires_at'])
            is_expired = datetime.now() >= expires_at
            
            # Return token data with expiry status - let caller decide to refresh
            return {
                **token_data,
                'is_expired': is_expired,
                'expires_at_dt': expires_at
            }
        except Exception as e:
            logger.error(f"Error getting token: {str(e)}")
            return None
    
    def get_valid_token(self, email: str) -> Optional[Dict]:
        """Get OAuth2 token ONLY if not expired"""
        token_data = self.get_token(email)
        if token_data and not token_data.get('is_expired', True):
            return token_data
        return None
    
    def has_refresh_token(self, email: str) -> bool:
        """Check if we have a refresh token for this email"""
        token_data = self.get_token(email)
        return token_data is not None and bool(token_data.get('refresh_token'))
    
    def delete_token(self, email: str):
        """Delete OAuth2 token for an email account"""
        try:
            tokens = self._load_tokens()
            if email in tokens:
                del tokens[email]
                with open(self.storage_file, 'w') as f:
                    json.dump(tokens, f, indent=2)
                logger.info(f"Deleted token for {email}")
        except Exception as e:
            logger.error(f"Error deleting token: {str(e)}")
    
    def _load_tokens(self) -> Dict:
        """Load all tokens from storage"""
        try:
            with open(self.storage_file, 'r') as f:
                return json.load(f)
        except:
            return {}

# Global instance
_token_storage = None

def get_token_storage() -> TokenStorage:
    """Get global token storage instance"""
    global _token_storage
    if _token_storage is None:
        _token_storage = TokenStorage()
    return _token_storage

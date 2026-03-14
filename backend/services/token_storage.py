"""
OAuth2 Token Storage Service
Stores and manages access tokens for email accounts
GCS-backed for persistence across Cloud Run restarts
"""
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

GCS_TOKEN_BLOB_PATH = "config/oauth_tokens.json"

# Cached GCS bucket (avoids recreating storage.Client() on every call)
_gcs_bucket_cache = None
_gcs_bucket_checked = False


def _get_gcs_bucket():
    """Get GCS bucket for token backup (lazy, cached, no crash if unavailable)"""
    global _gcs_bucket_cache, _gcs_bucket_checked
    if _gcs_bucket_checked:
        return _gcs_bucket_cache
    try:
        bucket_name = os.getenv("GCS_BUCKET_NAME", "efforts-recruitment-ai-data")
        is_production = (
            (os.getenv("PYTHON_ENV", "").lower() == "production")
            or bool(os.getenv("K_SERVICE"))
            or (os.getenv("ENVIRONMENT", "").lower() == "production")
        )
        if not is_production:
            _gcs_bucket_checked = True
            return None
        from google.cloud import storage
        client = storage.Client()
        _gcs_bucket_cache = client.bucket(bucket_name)
        _gcs_bucket_checked = True
        return _gcs_bucket_cache
    except Exception:
        _gcs_bucket_checked = True
        return None


class TokenStorage:
    """Store OAuth2 tokens in a JSON file with GCS backup for Cloud Run persistence"""
    
    def __init__(self, storage_file: str = "oauth_tokens.json"):
        self.storage_file = storage_file
        self._gcs_restored = False
        self._lock = threading.Lock()
        self._ensure_storage_exists()
    
    def _ensure_storage_exists(self):
        """Create storage file if it doesn't exist, restore from GCS first"""
        if not os.path.exists(self.storage_file):
            # Try restoring from GCS before creating empty file
            if self._restore_from_gcs():
                return
            with open(self.storage_file, 'w') as f:
                json.dump({}, f)
    
    def _restore_from_gcs(self) -> bool:
        """Download oauth_tokens.json from GCS on startup"""
        try:
            bucket = _get_gcs_bucket()
            if not bucket:
                return False
            blob = bucket.blob(GCS_TOKEN_BLOB_PATH)
            if not blob.exists():
                logger.info("💾 GCS token restore: No token backup found in GCS")
                return False
            blob.download_to_filename(self.storage_file)
            logger.info(f"✅ GCS token restore: Downloaded oauth_tokens.json from GCS")
            self._gcs_restored = True
            return True
        except Exception as e:
            logger.warning(f"⚠️ GCS token restore failed: {e}")
            return False
    
    def _backup_to_gcs(self):
        """Upload oauth_tokens.json to GCS after every save"""
        try:
            bucket = _get_gcs_bucket()
            if not bucket:
                return
            if not os.path.exists(self.storage_file):
                return
            blob = bucket.blob(GCS_TOKEN_BLOB_PATH)
            blob.upload_from_filename(self.storage_file, timeout=30)
            logger.info(f"✅ GCS token backup: Uploaded oauth_tokens.json to GCS")
        except Exception as e:
            logger.warning(f"⚠️ GCS token backup failed: {e}")
    
    def _delete_from_gcs(self):
        """Delete oauth_tokens.json from GCS"""
        try:
            bucket = _get_gcs_bucket()
            if not bucket:
                return
            blob = bucket.blob(GCS_TOKEN_BLOB_PATH)
            if blob.exists():
                blob.delete()
                logger.info("🗑️ GCS token delete: Removed oauth_tokens.json from GCS")
        except Exception as e:
            logger.warning(f"⚠️ GCS token delete failed: {e}")
    
    def save_token(self, email: str, access_token: str, refresh_token: Optional[str], expires_in: int, auth_type: str = 'delegated'):
        """Save OAuth2 token for an email account (local + GCS backup). Thread-safe."""
        success = False
        with self._lock:
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

                logger.info(f"✅ Saved OAuth2 token for {email} (auth_type={auth_type}, has_refresh={bool(refresh_token)})")
                success = True
            except Exception as e:
                logger.error(f"Error saving token: {str(e)}")
        # Outside the lock — run GCS backup in background to avoid blocking token operations
        if success:
            threading.Thread(target=self._backup_to_gcs, daemon=True).start()
        return success
    
    def get_token(self, email: str) -> Optional[Dict]:
        """Get OAuth2 token for an email account, with expiry status. Thread-safe."""
        with self._lock:
            try:
                tokens = self._load_tokens()
                token_data = tokens.get(email)

                # If no local token, try GCS restore (instance may have restarted)
                if not token_data and not self._gcs_restored:
                    if self._restore_from_gcs():
                        tokens = self._load_tokens()
                        token_data = tokens.get(email)
                    self._gcs_restored = True  # Only try once per instance lifetime

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
    
    def delete_token(self, email: str, delete_from_gcs: bool = False):
        """Delete OAuth2 token for an email account. Thread-safe.
        Always syncs deletion to GCS backup (so deleted token isn't restored on restart).
        Only fully removes GCS file when delete_from_gcs=True."""
        do_backup = False
        with self._lock:
            try:
                tokens = self._load_tokens()
                if email in tokens:
                    del tokens[email]
                    with open(self.storage_file, 'w') as f:
                        json.dump(tokens, f, indent=2)
                    # Always backup the updated (token-removed) file to GCS
                    # so the deletion persists across restarts
                    if delete_from_gcs:
                        self._delete_from_gcs()
                    else:
                        do_backup = True
                    logger.info(f"Deleted token for {email} (gcs_delete={delete_from_gcs})")
            except Exception as e:
                logger.error(f"Error deleting token: {str(e)}")
        # Outside the lock — run GCS backup in background to avoid blocking token operations
        if do_backup:
            threading.Thread(target=self._backup_to_gcs, daemon=True).start()
    
    def _load_tokens(self) -> Dict:
        """Load all tokens from storage"""
        try:
            if not os.path.exists(self.storage_file):
                return {}
            with open(self.storage_file, 'r') as f:
                return json.load(f)
        except Exception:
            return {}

# Global instance (thread-safe initialization)
_token_storage = None
_token_storage_lock = threading.Lock()

def get_token_storage() -> TokenStorage:
    """Get global token storage instance (thread-safe)"""
    global _token_storage
    if _token_storage is None:
        with _token_storage_lock:
            if _token_storage is None:
                _token_storage = TokenStorage()
    return _token_storage

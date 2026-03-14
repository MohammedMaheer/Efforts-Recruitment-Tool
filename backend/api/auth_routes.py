"""Authentication routes — login, register, profile, password management."""
import os
import time
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.dependencies import require_auth, require_admin
from api.deps import get_auth, _login_attempts, _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
user_router = APIRouter(prefix="/api/users", tags=["users"])


# ── Pydantic Models ──────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    username: Optional[str] = None

class UserProfile(BaseModel):
    firstName: str
    lastName: str
    email: str
    company: Optional[str] = None
    phone: Optional[str] = None

class PasswordUpdate(BaseModel):
    currentPassword: str
    newPassword: str


# ── Routes ───────────────────────────────────────────────────────────────

@router.post("/login")
async def login(request: LoginRequest):
    """
    Authenticate user and return JWT token.
    Rate limited: max 5 attempts per 15 minutes per email.
    """
    try:
        if not request.email or not request.password:
            raise HTTPException(400, "Email and password are required")

        login_key = request.email.strip().lower()
        now = time.time()
        attempts = _login_attempts.get(login_key, [])
        attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "Too many login attempts. Please try again in 15 minutes.")

        try:
            auth_service = get_auth()
            result = auth_service.login(request.email, request.password)
            _login_attempts.pop(login_key, None)
            return result
        except ValueError:
            attempts.append(now)
            _login_attempts[login_key] = attempts
            if len(_login_attempts) > 10000:
                # Evict only expired entries — do not wipe valid rate-limit records
                cutoff = now - 900
                expired_keys = [k for k, v in _login_attempts.items() if not v or max(v) < cutoff]
                for k in expired_keys:
                    del _login_attempts[k]
            raise HTTPException(401, "Invalid credentials")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(500, "Login failed. Please try again later.")


@router.post("/register")
async def register(request: RegisterRequest):
    """
    Register new user account.
    Registration can be disabled via REGISTRATION_ENABLED=false env var.
    """
    try:
        reg_enabled = os.getenv('REGISTRATION_ENABLED', 'true').lower() == 'true'
        if not reg_enabled:
            raise HTTPException(403, "Registration is disabled. Contact an administrator.")

        if not request.email or not request.password or not request.name:
            raise HTTPException(400, "Name, email and password are required")

        auth_service = get_auth()
        result = auth_service.register(
            email=request.email,
            password=request.password,
            name=request.name,
            username=request.username,
        )
        logger.info(f"✅ New user registered: {request.email} ({request.name})")
        return result

    except ValueError as e:
        raise HTTPException(400, str(e) if str(e) else "Invalid registration data")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(500, "Registration error")


@router.get("/me")
async def get_current_user(current_user: dict = Depends(require_auth)):
    """Get current user from JWT token."""
    return {"user": current_user}


# ── User Profile Routes ─────────────────────────────────────────────────

@user_router.put("/profile")
async def update_profile(profile: UserProfile, current_user: dict = Depends(require_auth)):
    """Update user profile information."""
    try:
        auth_service = get_auth()
        updated_user = auth_service.update_profile(current_user['id'], {
            'name': f"{profile.firstName} {profile.lastName}",
            'first_name': profile.firstName,
            'company': profile.company,
            'phone': profile.phone,
        })
        return {'status': 'success', 'message': 'Profile updated successfully', 'user': updated_user}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e) if str(e) else "Invalid profile data")
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        raise HTTPException(500, "Error updating profile")


@user_router.put("/password")
async def update_password(password_update: PasswordUpdate, current_user: dict = Depends(require_auth)):
    """Update user password."""
    try:
        auth_service = get_auth()
        auth_service.change_password(
            current_user['id'],
            password_update.currentPassword,
            password_update.newPassword,
        )
        return {'status': 'success', 'message': 'Password updated successfully'}
    except ValueError as e:
        raise HTTPException(400, str(e) if str(e) else "Invalid password data")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password update error: {e}")
        raise HTTPException(500, "Error updating password")

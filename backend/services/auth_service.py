"""
Authentication Service
Handles user registration, login, and JWT token management
"""

import sqlite3
import os
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 days

def _hash_password(password: str) -> str:
    """Hash password using SHA-256 with salt"""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${hashed}"

def _verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify password against stored hash"""
    try:
        salt, hashed = stored_hash.split('$')
        return hashlib.sha256((salt + plain_password).encode()).hexdigest() == hashed
    except:
        return False

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 days

class AuthService:
    """Service for user authentication and authorization"""
    
    def __init__(self, db_path: str = "recruitment.db"):
        self.db_path = db_path
        self._init_users_table()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_users_table(self):
        """Initialize users table"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    username TEXT UNIQUE,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    role TEXT DEFAULT 'Recruiter',
                    company TEXT,
                    phone TEXT,
                    avatar_url TEXT,
                    is_active INTEGER DEFAULT 1,
                    email_verified INTEGER DEFAULT 0,
                    last_login TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active)")
            
            conn.commit()
    
    def _hash_password(self, password: str) -> str:
        """Hash password using SHA-256 with salt"""
        return _hash_password(password)
    
    def _verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password against stored hash"""
        return _verify_password(plain_password, hashed_password)
    
    def _create_access_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()
        expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
        to_encode.update({"exp": expire, "iat": datetime.utcnow()})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    def _generate_user_id(self) -> str:
        """Generate unique user ID"""
        return f"user_{secrets.token_hex(8)}"
    
    def register(self, email: str, password: str, name: str, username: Optional[str] = None) -> Dict[str, Any]:
        """
        Register a new user
        
        Args:
            email: User's email address
            password: Plain text password (will be hashed)
            name: User's full name
            username: Optional username for login
            
        Returns:
            Dict with user data and access token
            
        Raises:
            ValueError: If email already exists or validation fails
        """
        # Validate inputs
        email = email.strip().lower()
        if not email or '@' not in email:
            raise ValueError("Invalid email address")
        
        if not name or len(name.strip()) < 2:
            raise ValueError("Name is required")
        
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters")
        
        # Process name
        name = name.strip()
        name_parts = name.split()
        first_name = name_parts[0] if name_parts else "User"
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ""
        
        # Generate username from email if not provided
        if not username:
            username = email.split('@')[0].lower().replace('.', '_').replace('-', '_')
        else:
            username = username.strip().lower()
        
        # Generate user data
        user_id = self._generate_user_id()
        password_hash = self._hash_password(password)
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (id, email, username, password_hash, name, first_name, last_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, email, username, password_hash, name, first_name, last_name,
                    datetime.utcnow().isoformat(), datetime.utcnow().isoformat()
                ))
                conn.commit()
        except sqlite3.IntegrityError as e:
            if 'email' in str(e).lower():
                raise ValueError("An account with this email already exists")
            elif 'username' in str(e).lower():
                raise ValueError("This username is already taken")
            raise ValueError("An account with this email already exists")
        
        # Create access token
        token = self._create_access_token({"sub": user_id, "email": email, "username": username})
        
        return {
            "user": {
                "id": user_id,
                "email": email,
                "username": username,
                "name": name,
                "firstName": first_name,
                "lastName": last_name,
                "role": "Recruiter"
            },
            "token": token
        }
    
    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user and return access token
        Supports login with email or username
        
        Args:
            email: User's email address or username
            password: Plain text password
            
        Returns:
            Dict with user data and access token
            
        Raises:
            ValueError: If credentials are invalid
        """
        login_id = email.strip().lower()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Try email first, then username
            cursor.execute("""
                SELECT id, email, username, password_hash, name, first_name, last_name, role, company, phone, avatar_url
                FROM users WHERE (email = ? OR username = ?) AND is_active = 1
            """, (login_id, login_id))
            row = cursor.fetchone()
        
        if not row:
            raise ValueError("Invalid email/username or password")
        
        if not self._verify_password(password, row['password_hash']):
            raise ValueError("Invalid email/username or password")
        
        # Update last login
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET last_login = ? WHERE id = ?
            """, (datetime.utcnow().isoformat(), row['id']))
            conn.commit()
        
        # Create access token
        token = self._create_access_token({"sub": row['id'], "email": row['email'], "username": row['username']})
        
        return {
            "user": {
                "id": row['id'],
                "email": row['email'],
                "username": row['username'],
                "name": row['name'],
                "firstName": row['first_name'],
                "lastName": row['last_name'],
                "role": row['role'] or "Recruiter",
                "company": row['company'],
                "phone": row['phone'],
                "avatarUrl": row['avatar_url']
            },
            "token": token
        }
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify JWT token and return user data
        
        Args:
            token: JWT access token
            
        Returns:
            User data if token is valid, None otherwise
        """
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            
            if not user_id:
                return None
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, email, name, first_name, role, company, phone, avatar_url
                    FROM users WHERE id = ? AND is_active = 1
                """, (user_id,))
                row = cursor.fetchone()
            
            if not row:
                return None
            
            return {
                "id": row['id'],
                "email": row['email'],
                "name": row['name'],
                "firstName": row['first_name'],
                "role": row['role'] or "Recruiter",
                "company": row['company'],
                "phone": row['phone'],
                "avatarUrl": row['avatar_url']
            }
            
        except JWTError:
            return None
    
    def update_profile(self, user_id: str, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update user profile
        
        Args:
            user_id: User ID
            profile_data: Dict with profile fields to update
            
        Returns:
            Updated user data
        """
        allowed_fields = ['name', 'first_name', 'company', 'phone', 'avatar_url']
        update_fields = []
        update_values = []
        
        for field in allowed_fields:
            if field in profile_data:
                update_fields.append(f"{field} = ?")
                update_values.append(profile_data[field])
        
        if not update_fields:
            raise ValueError("No valid fields to update")
        
        update_values.append(datetime.utcnow().isoformat())
        update_values.append(user_id)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE users SET {', '.join(update_fields)}, updated_at = ?
                WHERE id = ?
            """, update_values)
            conn.commit()
            
            # Fetch updated user
            cursor.execute("""
                SELECT id, email, name, first_name, role, company, phone, avatar_url
                FROM users WHERE id = ?
            """, (user_id,))
            row = cursor.fetchone()
        
        if not row:
            raise ValueError("User not found")
        
        return {
            "id": row['id'],
            "email": row['email'],
            "name": row['name'],
            "firstName": row['first_name'],
            "role": row['role'] or "Recruiter",
            "company": row['company'],
            "phone": row['phone'],
            "avatarUrl": row['avatar_url']
        }
    
    def change_password(self, user_id: str, current_password: str, new_password: str) -> bool:
        """
        Change user password
        
        Args:
            user_id: User ID
            current_password: Current password for verification
            new_password: New password
            
        Returns:
            True if successful
            
        Raises:
            ValueError: If current password is incorrect or validation fails
        """
        if len(new_password) < 6:
            raise ValueError("New password must be at least 6 characters")
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
        
        if not row:
            raise ValueError("User not found")
        
        if not self._verify_password(current_password, row['password_hash']):
            raise ValueError("Current password is incorrect")
        
        new_hash = self._hash_password(new_password)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET password_hash = ?, updated_at = ?
                WHERE id = ?
            """, (new_hash, datetime.utcnow().isoformat(), user_id))
            conn.commit()
        
        return True
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, email, name, first_name, role, company, phone, avatar_url
                FROM users WHERE id = ? AND is_active = 1
            """, (user_id,))
            row = cursor.fetchone()
        
        if not row:
            return None
        
        return {
            "id": row['id'],
            "email": row['email'],
            "name": row['name'],
            "firstName": row['first_name'],
            "role": row['role'] or "Recruiter",
            "company": row['company'],
            "phone": row['phone'],
            "avatarUrl": row['avatar_url']
        }
    
    def delete_user(self, user_id: str) -> bool:
        """Soft delete user (set is_active = 0)"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET is_active = 0, updated_at = ?
                WHERE id = ?
            """, (datetime.utcnow().isoformat(), user_id))
            conn.commit()
        return True


# Singleton instance
_auth_service = None

def get_auth_service(db_path: str = "recruitment.db") -> AuthService:
    """Get or create auth service singleton"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(db_path)
    return _auth_service

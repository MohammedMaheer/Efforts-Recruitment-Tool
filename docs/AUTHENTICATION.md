# Authentication System

## Overview

The AI Recruiter Platform uses a JWT-based authentication system with secure password hashing and persistent user sessions.

## Default Admin Credentials

| Field | Value |
|-------|-------|
| **Name** | Mohammed Maheer |
| **Email** | admin@effortz.com |
| **Username** | admin |
| **Password** | admin123 |

> ⚠️ **Important**: Change the admin password after first login in production!

## Features

- **User Registration**: Create new accounts with email, password, and optional name
- **User Login**: Authenticate with email and password
- **JWT Tokens**: Secure session management with 7-day expiration
- **Password Hashing**: bcrypt-based secure password storage
- **Token Verification**: Auto-verify tokens on app load
- **Profile Management**: Update user profile and change password
- **Persistent Sessions**: Sessions persist across browser refreshes

## API Endpoints

### Register New User

```http
POST /api/auth/register
Content-Type: application/json

{
  "email": "user@company.com",
  "password": "securePassword123",
  "name": "John Doe"  // Optional
}
```

**Response:**
```json
{
  "user": {
    "id": "user_abc123",
    "email": "user@company.com",
    "name": "John Doe",
    "firstName": "John",
    "role": "Recruiter"
  },
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### Login

```http
POST /api/auth/login
Content-Type: application/json

{
  "email": "user@company.com",
  "password": "securePassword123"
}
```

**Response:** Same as register

### Get Current User

```http
GET /api/auth/me
Authorization: Bearer <token>
```

**Response:**
```json
{
  "user": {
    "id": "user_abc123",
    "email": "user@company.com",
    "name": "John Doe",
    "firstName": "John",
    "role": "Recruiter"
  }
}
```

### Update Profile

```http
PUT /api/users/profile
Authorization: Bearer <token>
Content-Type: application/json

{
  "firstName": "John",
  "lastName": "Doe",
  "email": "user@company.com",
  "company": "Acme Corp",
  "phone": "+971501234567"
}
```

### Change Password

```http
PUT /api/users/password
Authorization: Bearer <token>
Content-Type: application/json

{
  "currentPassword": "oldPassword123",
  "newPassword": "newSecurePassword456"
}
```

## Frontend Usage

### Login

```typescript
import { useAuthStore } from '@/store/authStore'

const { login } = useAuthStore()

try {
  await login('user@company.com', 'password123')
  // User is now authenticated
} catch (error) {
  console.error('Login failed:', error.message)
}
```

### Register

```typescript
const { register } = useAuthStore()

try {
  await register('user@company.com', 'password123', 'John Doe')
  // User is registered and authenticated
} catch (error) {
  console.error('Registration failed:', error.message)
}
```

### Check Authentication

```typescript
const { isAuthenticated, user, token } = useAuthStore()

if (isAuthenticated) {
  console.log(`Logged in as ${user.name}`)
}
```

### Logout

```typescript
const { logout } = useAuthStore()
logout()
// User is now logged out
```

### Verify Token on App Load

```typescript
const { verifyToken } = useAuthStore()

useEffect(() => {
  verifyToken() // Returns true if valid, false if expired
}, [])
```

## Security Features

### Password Requirements
- Minimum 6 characters
- Hashed using bcrypt before storage

### JWT Token
- Algorithm: HS256
- Expiration: 7 days
- Contains: user ID, email, issued at, expiration

### Database Schema

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  first_name TEXT,
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
```

## Error Handling

| Status Code | Meaning |
|-------------|---------|
| 400 | Bad request (validation error) |
| 401 | Invalid credentials or expired token |
| 500 | Server error |

## Environment Variables

```env
# Optional - auto-generated if not set
JWT_SECRET_KEY=your-secret-key-here
```

## Testing

### Test Login
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "test@company.com", "password": "test123"}'
```

### Test Registration
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "newuser@company.com", "password": "secure123", "name": "New User"}'
```

### Test Token Verification
```bash
curl -X GET http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

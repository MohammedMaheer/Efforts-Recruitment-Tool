# OAuth2 Setup Guide for Office 365

OAuth2 provides secure, modern authentication for Office 365/Outlook accounts without requiring app passwords.

## Prerequisites

- Azure AD tenant (Office 365 organization)
- Admin access to Azure Portal (or delegated app registration permissions)
- Your Office 365 email account

## Step 1: Register Application in Azure Portal

1. **Go to Azure Portal**
   - Visit: https://portal.azure.com
   - Sign in with your Office 365 account

2. **Navigate to App Registrations**
   - Search for "App registrations" in the search bar
   - Click "+ New registration"

3. **Register the Application**
   - **Name**: `AI Recruitment Tool` (or any name)
   - **Supported account types**: 
     - Select "Accounts in this organizational directory only" (single tenant)
     - OR "Accounts in any organizational directory" (multi-tenant) if needed
   - **Redirect URI**: 
     - Platform: Web
     - URI: `http://localhost:3000/auth/callback`
   - Click **Register**

4. **Note the Application (client) ID**
   - Copy the "Application (client) ID" - you'll need this
   - Copy the "Directory (tenant) ID" - you'll need this too

## Step 2: Create Client Secret

1. **Go to "Certificates & secrets"** (left sidebar)
2. Click **"+ New client secret"**
3. **Description**: `AI Recruitment Tool Secret`
4. **Expires**: Choose duration (6 months, 12 months, or 24 months recommended)
5. Click **Add**
6. **IMPORTANT**: Copy the "Value" immediately - it won't be shown again!

## Step 3: Configure API Permissions

1. **Go to "API permissions"** (left sidebar)
2. Click **"+ Add a permission"**
3. Select **"Microsoft Graph"**
4. Select **"Delegated permissions"**
5. Add these permissions:
   - `Mail.Read` - Read user mail
   - `Mail.ReadWrite` - Read and write user mail
   - `User.Read` - Read user profile
6. Click **Add permissions**
7. **IMPORTANT**: Click **"Grant admin consent for [Your Organization]"**
   - This step requires admin privileges
   - If you don't have admin access, ask your IT admin to grant consent

## Step 4: Configure .env File

Add these lines to your `backend/.env`:

```env
# Microsoft OAuth2 Configuration
MICROSOFT_CLIENT_ID=YOUR_APPLICATION_CLIENT_ID_HERE
MICROSOFT_CLIENT_SECRET=YOUR_CLIENT_SECRET_VALUE_HERE
MICROSOFT_TENANT_ID=YOUR_TENANT_ID_HERE
OAUTH_REDIRECT_URI=http://localhost:8000/api/oauth2/callback

# OAuth Automation (Optional)
AUTO_SYNC_ENABLED=true
SYNC_INTERVAL_MINUTES=15
AUTO_TOKEN_REFRESH=true
```

Replace:
- `YOUR_APPLICATION_CLIENT_ID_HERE` with the Application (client) ID from Step 1
- `YOUR_CLIENT_SECRET_VALUE_HERE` with the secret value from Step 2
- `YOUR_TENANT_ID_HERE` with the Directory (tenant) ID from Step 1

## OAuth2 Automation Service (New!)

The platform now includes automatic OAuth2 token management:

- **Auto Token Refresh**: Tokens automatically refresh before expiry
- **Background Email Sync**: Continuous email monitoring and candidate extraction
- **Status Monitoring**: Track auth status, sync progress, and statistics

### API Endpoints

```bash
# Check OAuth status
curl http://localhost:8000/api/oauth/status

# Manual token refresh
curl -X POST http://localhost:8000/api/oauth/refresh

# Trigger manual sync
curl -X POST http://localhost:8000/api/oauth/sync
```

## Step 5: Update Frontend Redirect URI (if different)

If your frontend runs on a different URL, update:
1. Azure Portal → Your App → Authentication → Add platform → Web
2. Add your production URL: `https://yourdomain.com/auth/callback`
3. Update `MICROSOFT_REDIRECT_URI` in .env

## Step 6: Test OAuth2 Authentication

### Option A: Using API Endpoints

1. **Get Authorization URL**
   ```bash
   curl http://localhost:8000/api/email/oauth2/authorize?provider=outlook&redirect_uri=http://localhost:3000/auth/callback
   ```

2. **Visit the returned authorization URL in browser**
   - You'll be redirected to Microsoft login
   - Grant permissions
   - You'll be redirected back with a `code` parameter

3. **Exchange code for token**
   ```bash
   curl -X POST http://localhost:8000/api/email/oauth2/callback \
     -H "Content-Type: application/json" \
     -d '{
       "code": "AUTHORIZATION_CODE_FROM_REDIRECT",
       "redirect_uri": "http://localhost:3000/auth/callback"
     }'
   ```

### Option B: Using Frontend UI

1. Open the application: http://localhost:3000
2. Go to **Email Integration** section
3. Select **"Outlook"** as provider
4. Click **"Connect with OAuth2"** button
5. Sign in with your Office 365 account
6. Grant permissions
7. You'll be redirected back and automatically connected

## Troubleshooting

### Error: "AADSTS50011: The redirect URI specified in the request does not match"

**Solution**: Make sure the redirect URI in your code matches exactly what's registered in Azure Portal. Check:
- No trailing slashes
- Correct protocol (http vs https)
- Correct port number

### Error: "AADSTS65001: The user or administrator has not consented"

**Solution**: 
1. Go to Azure Portal → Your App → API permissions
2. Click "Grant admin consent for [Your Organization]"
3. If you don't have admin access, ask your IT admin

### Error: "Invalid client secret"

**Solution**: The client secret has expired or is incorrect.
1. Go to Azure Portal → Your App → Certificates & secrets
2. Delete old secret
3. Create new client secret
4. Update `MICROSOFT_CLIENT_SECRET` in .env
5. Restart backend server

### Error: "Insufficient privileges to complete the operation"

**Solution**: Your account doesn't have the required permissions.
1. Check if Mail.Read and Mail.ReadWrite permissions are granted
2. Ensure admin consent was granted
3. Wait 5-10 minutes for permissions to propagate

## Security Best Practices

1. **Never commit secrets**: Add `.env` to `.gitignore`
2. **Use environment variables**: Never hardcode credentials
3. **Rotate secrets regularly**: Generate new client secrets every 6-12 months
4. **Limit scope**: Only request permissions you actually need
5. **Use HTTPS in production**: Never use OAuth2 over HTTP in production

## Alternative: App Password (Simpler but Less Secure)

If OAuth2 is too complex for your use case:

1. Go to https://account.microsoft.com/security
2. Under "Security basics", click "App passwords"
3. Generate a new app password
4. Use this password instead of OAuth2:
   ```env
   EMAIL_ADDRESS=hr@effortz.com
   EMAIL_PASSWORD=your-app-password-here
   ```

**Note**: App passwords may not work for organizational accounts with strict security policies.

## API Endpoints Reference

### Get Authorization URL
```
GET /api/email/oauth2/authorize
Query params:
  - provider: outlook|microsoft|office365
  - redirect_uri: Your callback URL

Response:
{
  "status": "success",
  "authorization_url": "https://login.microsoftonline.com/...",
  "provider": "microsoft"
}
```

### Exchange Authorization Code for Token
```
POST /api/email/oauth2/callback
Body:
{
  "code": "authorization_code_from_redirect",
  "redirect_uri": "http://localhost:3000/auth/callback",
  "state": "optional_state_parameter"
}

Response:
{
  "status": "connected",
  "access_token": "eyJ0eXAiOiJKV1QiLC...",
  "expires_in": 3600,
  "provider": "microsoft",
  "message": "Successfully authenticated with Microsoft"
}
```

### Sync Emails with OAuth2
```
POST /api/email/sync
Body:
{
  "provider": "outlook",
  "email": "hr@effortz.com",
  "access_token": "eyJ0eXAiOiJKV1QiLC...",
  "folder": "INBOX",
  "limit": 50
}

Response:
{
  "status": "success",
  "candidates_found": 15,
  "candidates": [...],
  "auth_type": "oauth2"
}
```

## Support

If you encounter issues:
1. Check the backend logs for detailed error messages
2. Verify all settings in Azure Portal match your .env file
3. Ensure your Office 365 account has the necessary permissions
4. Contact your IT admin if you need help with Azure Portal access

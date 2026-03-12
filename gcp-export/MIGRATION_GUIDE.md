# GCP Project Migration Guide
## From: `efforts-recruitment` → New GCP Project

**Exported on:** March 10, 2026  
**Current billing status:** DISABLED (delinquent)  
**Current Cloud Run revision:** `recruitment-backend-00260-7gm`

---

## What's in this Export (`gcp-export/`)

### ✅ Successfully Exported
| File | Description | Size |
|------|-------------|------|
| `cloud-run/service-config.yaml` | Full Cloud Run service YAML (env vars, resources, scaling) | ~5KB |
| `cloud-run/env-vars.env` | Environment variables template (secrets need manual entry) | ~1KB |
| `cloud-run/recruitment-backend-image.tar` | Docker image (built from latest `release/v2.0` source) | ~1.4GB |
| `gcs-objects-listing.json` | Full listing of all GCS objects with sizes/metadata | ~3KB |
| `enabled-apis.json` | List of all enabled GCP APIs | ~15KB |
| `iam-policy.yaml` | IAM roles and permissions | ~2KB |

### ❌ Blocked by Billing (MUST re-enable old project OR re-create)
| Resource | Size | GCS Path |
|----------|------|----------|
| **recruitment.db** (main database, 4860 candidates) | 160 MB | `gs://efforts-recruitment-data/db/recruitment.db` |
| **candidates_backup.json** (JSON backup) | 50 MB | `gs://efforts-recruitment-data/backups/candidates_backup.json` |
| **oauth_tokens.json** (OAuth refresh tokens) | <1 KB | `gs://efforts-recruitment-data/config/oauth_tokens.json` |
| **DB Snapshots** (3 snapshots) | 480 MB | `gs://efforts-recruitment-data/db/snapshots/` |
| **10 Secrets** (API keys, passwords) | N/A | Secret Manager |

---

## Step-by-Step Migration to New GCP Project

### Step 1: Re-enable Billing on OLD Project (to download data)

This is the critical step. You MUST re-enable billing to download the database:

```
Visit: https://console.developers.google.com/billing/enable?project=efforts-recruitment
```

Once billing is re-enabled, run this to download everything:

```powershell
cd c:\Users\USER\Desktop\WORK\Efforts-Recruitment-Tool-main

# Download database
gcloud storage cp gs://efforts-recruitment-data/db/recruitment.db gcp-export/db/recruitment.db

# Download candidates backup 
gcloud storage cp gs://efforts-recruitment-data/backups/candidates_backup.json gcp-export/backups/candidates_backup.json

# Download OAuth tokens
gcloud storage cp gs://efforts-recruitment-data/config/oauth_tokens.json gcp-export/config/oauth_tokens.json

# Download DB snapshots
gcloud storage cp -r gs://efforts-recruitment-data/db/snapshots/ gcp-export/db/snapshots/

# Export all secrets
$secrets = @("SECRET_KEY", "JWT_SECRET_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID", "EMAIL_PASSWORD", "SMTP_PASSWORD", "CRON_SECRET")
foreach ($s in $secrets) {
    $val = gcloud secrets versions access latest --secret=$s --project=efforts-recruitment 2>$null
    if ($val) { Add-Content -Path "gcp-export\secrets\secrets.env" -Value "$s=$val"; Write-Host "$s exported" }
}
```

Then IMMEDIATELY disable billing again to stop charges:
```
Visit: https://console.cloud.google.com/billing/linkedaccount?project=efforts-recruitment
→ Unlink billing account
```

### Step 2: Create New GCP Project

```powershell
# Create project
gcloud projects create NEW-PROJECT-ID --name="Efforts Recruitment"

# Link billing
gcloud billing projects link NEW-PROJECT-ID --billing-account=NEW-BILLING-ACCOUNT-ID

# Enable required APIs
$apis = @(
    "run.googleapis.com",
    "cloudbuild.googleapis.com", 
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "generativelanguage.googleapis.com",
    "firebasehosting.googleapis.com",
    "firebase.googleapis.com",
    "cloudscheduler.googleapis.com"
)
foreach ($api in $apis) {
    gcloud services enable $api --project=NEW-PROJECT-ID
}
```

### Step 3: Create GCS Bucket & Upload Data

```powershell
# Create data bucket
gcloud storage buckets create gs://NEW-PROJECT-ID-data --location=us-central1 --project=NEW-PROJECT-ID

# Upload database
gcloud storage cp gcp-export/db/recruitment.db gs://NEW-PROJECT-ID-data/db/recruitment.db

# Upload candidates backup
gcloud storage cp gcp-export/backups/candidates_backup.json gs://NEW-PROJECT-ID-data/backups/candidates_backup.json

# Upload OAuth tokens (if exported)
gcloud storage cp gcp-export/config/oauth_tokens.json gs://NEW-PROJECT-ID-data/config/oauth_tokens.json
```

### Step 4: Create Secrets

```powershell
# Read secrets from exported file and create in new project
Get-Content gcp-export\secrets\secrets.env | ForEach-Object {
    $parts = $_ -split '=', 2
    $name = $parts[0]
    $value = $parts[1]
    Write-Output $value | gcloud secrets create $name --data-file=- --project=NEW-PROJECT-ID
    Write-Host "Created secret: $name"
}
```

### Step 5: Push Docker Image to New Project

```powershell
# Create Artifact Registry repo
gcloud artifacts repositories create cloud-run-source-deploy --location=us-central1 --repository-format=docker --project=NEW-PROJECT-ID

# Configure Docker auth
gcloud auth configure-docker us-central1-docker.pkg.dev

# Load the saved image
docker load -i gcp-export/cloud-run/recruitment-backend-image.tar

# Re-tag for new project
docker tag us-central1-docker.pkg.dev/efforts-recruitment/cloud-run-source-deploy/recruitment-backend:latest us-central1-docker.pkg.dev/NEW-PROJECT-ID/cloud-run-source-deploy/recruitment-backend:latest

# Push to new project
docker push us-central1-docker.pkg.dev/NEW-PROJECT-ID/cloud-run-source-deploy/recruitment-backend:latest
```

### Step 6: Deploy Cloud Run Service

```powershell
gcloud run deploy recruitment-backend `
    --image=us-central1-docker.pkg.dev/NEW-PROJECT-ID/cloud-run-source-deploy/recruitment-backend:latest `
    --project=NEW-PROJECT-ID `
    --region=us-central1 `
    --memory=4Gi `
    --timeout=300 `
    --allow-unauthenticated `
    --min-instances=0 `
    --max-instances=3 `
    --concurrency=40 `
    --set-env-vars="GEMINI_MODEL=gemini-2.5-flash,AI_TIMEOUT=120,GEMINI_DAILY_LIMIT=1500,GOOGLE_CLOUD_PROJECT=NEW-PROJECT-ID,EMAIL_ADDRESS=hr@effortz.com,GCS_BUCKET_NAME=NEW-PROJECT-ID-data" `
    --set-secrets="SECRET_KEY=SECRET_KEY:latest,JWT_SECRET_KEY=JWT_SECRET_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,MICROSOFT_CLIENT_ID=MICROSOFT_CLIENT_ID:latest,MICROSOFT_CLIENT_SECRET=MICROSOFT_CLIENT_SECRET:latest,MICROSOFT_TENANT_ID=MICROSOFT_TENANT_ID:latest,EMAIL_PASSWORD=EMAIL_PASSWORD:latest,SMTP_PASSWORD=SMTP_PASSWORD:latest,CRON_SECRET=CRON_SECRET:latest"
```

### Step 7: Update Frontend Config & Deploy

Update `src/config.ts` with the new Cloud Run URL:
```typescript
// Replace the backend URL with the new one
const BACKEND_URL = "https://recruitment-backend-XXXXXXXXXX.us-central1.run.app"
```

Then build and deploy:
```powershell
npx vite build
npx firebase deploy --only hosting --project=NEW-PROJECT-ID
```

### Step 8: Update Firebase Project (Optional)

If using a new Firebase project:
```powershell
firebase use NEW-PROJECT-ID
firebase init hosting  # Re-configure
firebase deploy --only hosting
```

### Step 9: Verify Deployment

```powershell
# Check backend health
Invoke-WebRequest -Uri "https://NEW-BACKEND-URL/api/health" -UseBasicParsing

# Check candidate count
Invoke-WebRequest -Uri "https://NEW-BACKEND-URL/api/candidates?limit=1" -UseBasicParsing
```

---

## Architecture Reference

| Component | Technology | Location |
|-----------|-----------|----------|
| Backend API | FastAPI (Python) | Cloud Run |
| Database | SQLite (persisted to GCS) | GCS bucket `*-data/db/` |
| Frontend | React + TypeScript + Vite | Firebase Hosting |
| AI Engine | Gemini 2.5 Flash | Google AI API |
| Secrets | 10 secrets | Secret Manager |
| Email | Microsoft OAuth + SMTP | External |
| Auth | JWT + session tokens | Backend |

## Cloud Run Resources
- **CPU:** 1 vCPU
- **Memory:** 4 GiB
- **Concurrency:** 40
- **Timeout:** 300s
- **Max instances:** 3
- **Min instances:** 0

## Secrets List (10 total)
1. `SECRET_KEY` - App secret key
2. `JWT_SECRET_KEY` - JWT signing key
3. `OPENAI_API_KEY` - OpenAI API key (fallback AI)
4. `GEMINI_API_KEY` - Google Gemini API key (primary AI)
5. `MICROSOFT_CLIENT_ID` - Microsoft OAuth client ID
6. `MICROSOFT_CLIENT_SECRET` - Microsoft OAuth client secret
7. `MICROSOFT_TENANT_ID` - Microsoft tenant ID
8. `EMAIL_PASSWORD` - Email account password
9. `SMTP_PASSWORD` - SMTP relay password
10. `CRON_SECRET` - Cron job authentication secret

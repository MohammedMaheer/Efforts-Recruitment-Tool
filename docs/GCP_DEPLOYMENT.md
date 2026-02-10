# GCP Deployment Guide - AI Recruitment Platform

## Prerequisites

1. **Google Cloud SDK** installed and configured
2. **GCP Project** created with billing enabled
3. **APIs enabled:** Cloud Run, Cloud Build, Container Registry, Secret Manager

## Quick Deploy (Cloud Run)

### 1. Set up GCP project

```bash
# Set your project
gcloud config set project YOUR_PROJECT_ID
gcloud config set run/region us-central1

# Enable required APIs
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

### 2. Store secrets in Secret Manager

```bash
# Store your secrets
echo -n "your-openai-key" | gcloud secrets create OPENAI_API_KEY --data-file=-
echo -n "your-ms-client-id" | gcloud secrets create MICROSOFT_CLIENT_ID --data-file=-
echo -n "your-ms-client-secret" | gcloud secrets create MICROSOFT_CLIENT_SECRET --data-file=-
echo -n "your-ms-tenant-id" | gcloud secrets create MICROSOFT_TENANT_ID --data-file=-

# Grant Cloud Run access to secrets
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 3. Deploy using Cloud Build

```bash
# From project root
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_REGION=us-central1,_BACKEND_URL=https://recruitment-backend-YOUR_PROJECT_ID.run.app
```

### 4. Manual Deploy (Alternative)

```bash
# Build and push backend
docker build -t gcr.io/YOUR_PROJECT_ID/recruitment-backend -f backend/Dockerfile --build-arg REQUIREMENTS_FILE=requirements.production.txt ./backend
docker push gcr.io/YOUR_PROJECT_ID/recruitment-backend

# Deploy backend to Cloud Run
gcloud run deploy recruitment-backend \
  --image gcr.io/YOUR_PROJECT_ID/recruitment-backend \
  --region us-central1 \
  --memory 2Gi --cpu 2 \
  --timeout 300 \
  --max-instances 10 \
  --port 8000 \
  --allow-unauthenticated \
  --set-env-vars "ENVIRONMENT=production,DEBUG=false" \
  --update-secrets "OPENAI_API_KEY=OPENAI_API_KEY:latest"

# Get backend URL
BACKEND_URL=$(gcloud run services describe recruitment-backend --region us-central1 --format 'value(status.url)')

# Build and push frontend
docker build -t gcr.io/YOUR_PROJECT_ID/recruitment-frontend -f Dockerfile.frontend --build-arg VITE_API_URL=$BACKEND_URL .
docker push gcr.io/YOUR_PROJECT_ID/recruitment-frontend

# Deploy frontend
gcloud run deploy recruitment-frontend \
  --image gcr.io/YOUR_PROJECT_ID/recruitment-frontend \
  --region us-central1 \
  --memory 256Mi --cpu 1 \
  --max-instances 5 \
  --port 3000 \
  --allow-unauthenticated

# Update backend CORS with frontend URL
FRONTEND_URL=$(gcloud run services describe recruitment-frontend --region us-central1 --format 'value(status.url)')
gcloud run services update recruitment-backend \
  --region us-central1 \
  --update-env-vars "CORS_ORIGINS=$FRONTEND_URL"
```

## Architecture on GCP

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│   Frontend   │────▶│    Backend API   │────▶│   SQLite DB  │
│  Cloud Run   │     │   Cloud Run      │     │  (embedded)  │
│  (nginx)     │     │  (FastAPI)       │     │              │
└──────────────┘     └────────┬─────────┘     └──────────────┘
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
              ┌──────────┐ ┌─────┐ ┌──────────┐
              │  Ollama  │ │OpenAI│ │ MS Graph │
              │  (opt.)  │ │ API  │ │  Email   │
              └──────────┘ └─────┘ └──────────┘
```

## Important Notes

- **SQLite**: Works for single-instance Cloud Run. For multi-instance, migrate to Cloud SQL (PostgreSQL)
- **Ollama/LLM**: Not available on Cloud Run. Use OpenAI as primary AI, or deploy Ollama on a GCE VM
- **File Storage**: Resume uploads need Cloud Storage for persistence across instances
- **Secrets**: Never commit `.env` files. Use Secret Manager for all sensitive values
- **Custom Domain**: Configure via Cloud Run domain mappings

## Cost Estimate (Cloud Run)

- **Backend**: ~$5-15/month (auto-scales to 0 when idle)
- **Frontend**: ~$1-3/month (static serving, minimal compute)
- **OpenAI API**: Pay per use (~$0.01-0.03 per analysis)
- **Total**: ~$10-20/month for low-moderate usage

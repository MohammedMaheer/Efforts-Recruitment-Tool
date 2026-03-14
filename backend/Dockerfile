FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt requirements.production.txt ./

# Install Python dependencies (use production requirements if available)
ARG REQUIREMENTS_FILE=requirements.production.txt
RUN pip install --no-cache-dir -r ${REQUIREMENTS_FILE}

# Pre-download AI models during build into a shared location.
# The model is baked into the image so cold starts never hit HuggingFace.
ENV SENTENCE_TRANSFORMERS_HOME=/app/models
ENV HF_HOME=/app/models/huggingface

RUN mkdir -p /app/models && \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')" && \
    python -m spacy download en_core_web_sm

# After build, set offline mode so runtime never contacts HuggingFace
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

# Copy application code
COPY . .

# Create data directories and non-root user
RUN mkdir -p /app/data/uploads /app/data/campaigns /app/data/email_templates && \
    useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Run application
# We use uvicorn directly instead of gunicorn because:
# 1. Cloud Run manages process lifecycle (gunicorn's process management is redundant)
# 2. Gunicorn's worker timeout kills workers during long-running background tasks (email sync)
# 3. Single worker (w=1) means gunicorn adds overhead with no benefit
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 30 --log-level info"]

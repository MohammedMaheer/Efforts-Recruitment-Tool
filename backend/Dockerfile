FROM python:3.13-slim

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
ARG REQUIREMENTS_FILE=requirements.txt
RUN pip install --no-cache-dir -r ${REQUIREMENTS_FILE}

# Download AI models during build (optional, can be done at runtime too)
RUN pip install sentence-transformers spacy && \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')" && \
    python -m spacy download en_core_web_sm

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/data/uploads /app/data/campaigns /app/data/email_templates

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
# Development: uvicorn main:app --host 0.0.0.0 --port 8000
# Production: gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
CMD ["gunicorn", "main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--timeout", "300", "--graceful-timeout", "60", "--keep-alive", "5"]

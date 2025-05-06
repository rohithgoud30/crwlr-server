# Use Python 3.11 slim-bullseye as base
FROM python:3.11-slim-bullseye

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gnupg \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
    
# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    ENV=production

# Install Playwright browsers in headless context 
# Note: we need to set a user-agent when installing browsers in Docker
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install app dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install chromium --with-deps

# Copy project
COPY . .

# Copy credentials file if it exists, or create a placeholder
# (Service account credentials can also be provided via environment variables in production)
RUN if [ ! -f "/app/firebase-credentials.json" ]; then \
    echo '{"placeholder": "Replace with actual credentials or use environment auth"}' > /app/firebase-credentials.json; \
    fi

# Run gunicorn
CMD exec gunicorn app.main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind :$PORT --timeout 300 
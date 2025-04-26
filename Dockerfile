FROM python:3.11-slim

WORKDIR /app

# Set default branch name to unknown
ARG BRANCH_NAME=unknown
ENV BRANCH_NAME=${BRANCH_NAME}

# Install required system dependencies for Playwright AND git
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libatspi2.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-noto-color-emoji \
    fonts-freefont-ttf \
    fonts-liberation \
    xvfb \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install NLTK data
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"

# Install and set up Playwright with better browser management
RUN pip install --no-cache-dir playwright==1.42.0 && \
    playwright install chromium && \
    playwright install-deps chromium

# Copy application code 
COPY . .

# Set environment variables
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Install Playwright browsers required by the application
RUN python -m playwright install --with-deps

# Command to run the application using Gunicorn
# Use the standard port 8080 for Cloud Run
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", "--bind", "0.0.0.0:8080"] 
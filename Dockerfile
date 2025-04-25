FROM python:3.11-slim
# Note: Python 3.11 is used for better compatibility with cloud-sql-python-connector
# Using Python 3.12+ can cause deprecation warnings with cloud-sql-python-connector

WORKDIR /app

# Set default branch name to unknown
ARG BRANCH_NAME=unknown
ENV BRANCH_NAME=${BRANCH_NAME}

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
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
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
# Install dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install NLTK data for text processing
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"

# Install Playwright and browsers
RUN pip install playwright && \
    playwright install chromium --with-deps

# Copy application code
COPY . .

# Set environment variables
ENV PORT=8080

# Run the application using run.py which handles environment variables
CMD ["python", "run.py"] 
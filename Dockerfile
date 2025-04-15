FROM python:3.11-slim

WORKDIR /app

# Set default branch name to unknown
ARG BRANCH_NAME=unknown
ENV BRANCH_NAME=${BRANCH_NAME}

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and browsers
RUN pip install playwright && \
    playwright install chromium

# Copy application code
COPY . .

# Set environment variables
ENV PORT=8080

# Run the application using run.py which handles environment variables
CMD ["python", "run.py"] 
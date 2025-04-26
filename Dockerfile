FROM mcr.microsoft.com/playwright:v1.42.0-focal

WORKDIR /app

# Set default branch name to unknown
ARG BRANCH_NAME=unknown
ENV BRANCH_NAME=${BRANCH_NAME}

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Ensure fake-useragent is explicitly installed for browser emulation
RUN pip install --no-cache-dir fake-useragent==2.1.0

# Install NLTK data for text processing
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"

# Copy application code
COPY . .

# Set environment variables
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV DEBUG=pw:api,pw:browser*

# Playwright-specific environment variables for containerized environment
ENV PLAYWRIGHT_HEADLESS=true

# Run the application using run.py which handles environment variables
CMD ["python", "-u", "run.py"] 
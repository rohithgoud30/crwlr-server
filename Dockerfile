FROM python:3.11-slim

WORKDIR /app

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

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "${PORT}"] 
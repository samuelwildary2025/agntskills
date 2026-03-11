# Dockerfile for Easypanel - Queue-based Architecture
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (gcc and libpq-dev needed for psycopg2)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy supervisord config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create directory for logs if configured to file
RUN mkdir -p logs

# Expose port
EXPOSE 8000

# Command to run both web and worker via supervisor
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

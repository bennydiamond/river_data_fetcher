# Lightweight Python image for river data fetcher
FROM python:3.11-slim

# Install dependencies (tzdata needed for APScheduler ZoneInfo support)
RUN DEBIAN_FRONTEND=noninteractive TZ=America/Montreal apt-get update && \
    apt-get install -y tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python script and entrypoint
COPY river_data_fetcher.py .
COPY entrypoint.sh /entrypoint.sh

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

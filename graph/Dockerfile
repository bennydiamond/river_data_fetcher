# Use Playwright's official image which includes browsers
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

# Install dependencies
RUN apt-get update && \
    apt-get install -y fonts-dejavu-core && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python script, HTML, and entrypoint
COPY download_graph.py .
COPY index.html /app/index.html
COPY entrypoint.sh /entrypoint.sh
COPY backup_web_root.sh /app/backup_web_root.sh

# Make entrypoint executable
RUN chmod +x /entrypoint.sh /app/backup_web_root.sh

# Create necessary directories for data (not logs)
RUN mkdir -p /opt/graph_automation /var/www/html/graphs

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

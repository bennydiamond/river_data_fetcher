#!/bin/bash
set -e

# Default cron schedule: every 10 minutes
CRON_SCHEDULE="${FETCHER_CRON_SCHEDULE:-*/10 * * * *}"

echo "Setting up cron with schedule: ${CRON_SCHEDULE}"

# Create crontab dynamically - output to stdout/stderr (captured by Docker logging)
echo "${CRON_SCHEDULE} root cd /app && python3 /app/river_data_fetcher.py 2>&1" > /etc/cron.d/fetcher-cron
echo "" >> /etc/cron.d/fetcher-cron

# Set proper permissions
chmod 0644 /etc/cron.d/fetcher-cron

# Apply cron job
crontab /etc/cron.d/fetcher-cron

echo "Running initial fetch..."
# Run immediately on startup
python3 /app/river_data_fetcher.py 2>&1 || echo "Initial run failed, but continuing..."

echo "Starting cron daemon..."
# Start cron in foreground mode
cron

# Keep container running
echo "Cron daemon started. Container will remain running."
tail -f /dev/null

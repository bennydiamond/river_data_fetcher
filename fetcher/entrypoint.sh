#!/bin/bash
set -e

# Default cron schedule: every 10 minutes
CRON_SCHEDULE="${FETCHER_CRON_SCHEDULE:-*/10 * * * *}"

FETCHER_ARGS=()
if [ -n "${STATION_NUMBER:-}" ]; then
	FETCHER_ARGS+=("--station-number" "${STATION_NUMBER}")
fi
if [ -n "${HA_API_BASE_URL:-}" ]; then
	FETCHER_ARGS+=("--ha-api-base-url" "${HA_API_BASE_URL}")
fi
if [ -n "${DATA_URL:-}" ]; then
	FETCHER_ARGS+=("--data-url" "${DATA_URL}")
fi
if [ -n "${HA_TOKEN:-}" ]; then
	FETCHER_ARGS+=("--ha-token" "${HA_TOKEN}")
fi
if [ -n "${STATION_NAME_PREFIX:-}" ]; then
	FETCHER_ARGS+=("--station-name-prefix" "${STATION_NAME_PREFIX}")
fi
if [ -n "${RIVER_NAME:-}" ]; then
	FETCHER_ARGS+=("--river-name" "${RIVER_NAME}")
fi
if [ -n "${RIVER_NAME_FALLBACK:-}" ]; then
	FETCHER_ARGS+=("--river-name-fallback" "${RIVER_NAME_FALLBACK}")
fi

FETCHER_ARGS_STRING=""
for arg in "${FETCHER_ARGS[@]}"; do
	FETCHER_ARGS_STRING+=" $(printf '%q' "$arg")"
done

echo "Setting up cron with schedule: ${CRON_SCHEDULE}"

# Create crontab dynamically - output to stdout/stderr (captured by Docker logging)
echo "${CRON_SCHEDULE} root cd /app && python3 /app/river_data_fetcher.py${FETCHER_ARGS_STRING} 2>&1" > /etc/cron.d/fetcher-cron
echo "" >> /etc/cron.d/fetcher-cron

# Set proper permissions
chmod 0644 /etc/cron.d/fetcher-cron

# Apply cron job
crontab /etc/cron.d/fetcher-cron

echo "Running initial fetch..."
# Run immediately on startup
python3 /app/river_data_fetcher.py "${FETCHER_ARGS[@]}" 2>&1 || echo "Initial run failed, but continuing..."

echo "Starting cron daemon..."
# Start cron in foreground mode
cron

# Keep container running
echo "Cron daemon started. Container will remain running."
tail -f /dev/null

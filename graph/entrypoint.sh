#!/bin/bash
set -e

# Default cron schedule: every 2 hours at :05
CRON_SCHEDULE="${GRAPH_CRON_SCHEDULE:-5 */2 * * *}"
BACKUP_CRON_SCHEDULE="${GRAPH_BACKUP_CRON_SCHEDULE:-15 3 * * *}"

GRAPH_ARGS=()
if [ -n "${STATION_NUMBER:-}" ]; then
	GRAPH_ARGS+=("--station-number" "${STATION_NUMBER}")
fi
if [ -n "${GRAPH_URL:-}" ]; then
	GRAPH_ARGS+=("--graph-url" "${GRAPH_URL}")
fi

GRAPH_ARGS_STRING=""
for arg in "${GRAPH_ARGS[@]}"; do
	GRAPH_ARGS_STRING+=" $(printf '%q' "$arg")"
done

echo "Setting up cron with schedule: ${CRON_SCHEDULE}"

# Create crontab dynamically - output to stdout/stderr (captured by Docker logging)
CRON_FILE="/etc/cron.d/graph-cron"
{
	echo "${CRON_SCHEDULE} root cd /app && python3 /app/download_graph.py${GRAPH_ARGS_STRING} 2>&1"
	echo "${BACKUP_CRON_SCHEDULE} root /app/backup_web_root.sh 2>&1"
	echo ""
} > "${CRON_FILE}"

# Set proper permissions
chmod 0644 "${CRON_FILE}"

# Apply cron job
crontab "${CRON_FILE}"

# Create necessary directories (all in tmpfs)
WEB_ROOT="${WEB_ROOT:-/usr/share/nginx/html}"
LAST_SUCCESS_FILE="${LAST_SUCCESS_FILE:-/opt/graph_automation/last_success.json}"
BACKUP_DIR="${BACKUP_DIR:-/backup}"
mkdir -p "$(dirname "$LAST_SUCCESS_FILE")" "${WEB_ROOT}/graphs"

# Restore cached artifacts from persistent storage if present
if [ -d "${BACKUP_DIR}/graphs" ]; then
	if [ -n "$(ls -A "${BACKUP_DIR}/graphs" 2>/dev/null)" ]; then
		cp -a "${BACKUP_DIR}/graphs/." "${WEB_ROOT}/graphs/"
	fi
fi
if [ -f "${BACKUP_DIR}/last_success.json" ]; then
	cp -a "${BACKUP_DIR}/last_success.json" "${WEB_ROOT}/graphs/last_success.json"
	cp -a "${BACKUP_DIR}/last_success.json" "${LAST_SUCCESS_FILE}"
fi

# Apply stale overlay immediately if restored cache is too old
python3 /app/download_graph.py --check-stale "${GRAPH_ARGS[@]}" 2>&1 || true

# Copy index.html into RAM-based web root
if [ -f /app/index.html ]; then
	SOURCE_STATION_NUMBER="${STATION_NUMBER:-030315}"
	SOURCE_URL="${GRAPH_URL:-https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation=${SOURCE_STATION_NUMBER}}"
	SOURCE_URL_ESCAPED=$(printf '%s' "$SOURCE_URL" | sed 's/[&/]/\\&/g')
	sed "s#https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation=[0-9]*#${SOURCE_URL_ESCAPED}#g" /app/index.html > "${WEB_ROOT}/index.html"
fi

echo "Running initial graph download..."
python3 /app/download_graph.py "${GRAPH_ARGS[@]}" 2>&1 || echo "Initial run failed, but continuing..."

echo "Starting cron daemon..."
# Start cron in foreground mode
cron

# Keep container running
echo "Cron daemon started. Container will remain running."
tail -f /dev/null

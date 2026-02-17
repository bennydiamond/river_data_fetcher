#!/bin/bash
set -e

echo "Graph Downloader Container Starting"
echo "===================================="
echo ""

# Build CLI arguments from environment variables
GRAPH_ARGS=()
echo "Configuration:"
echo "  LOG_LEVEL=${LOG_LEVEL:-INFO}"
echo "  TZ=${TZ:-America/Montreal}"
echo "  GRAPH_INTERVAL_MINUTES=${GRAPH_INTERVAL_MINUTES:-120}"
echo "  BACKUP_INTERVAL_HOURS=${BACKUP_INTERVAL_HOURS:-24}"

if [ -n "${STATION_NUMBER:-}" ]; then
	echo "  STATION_NUMBER=${STATION_NUMBER}"
	GRAPH_ARGS+=("--station-number" "${STATION_NUMBER}")
fi
if [ -n "${GRAPH_URL:-}" ]; then
	echo "  GRAPH_URL=${GRAPH_URL}"
	GRAPH_ARGS+=("--graph-url" "${GRAPH_URL}")
fi

echo ""

# Create necessary directories (all in tmpfs)
WEB_ROOT="${WEB_ROOT:-/usr/share/nginx/html}"
LAST_SUCCESS_FILE="${LAST_SUCCESS_FILE:-/opt/graph_automation/last_success.json}"
BACKUP_DIR="${BACKUP_DIR:-/backup}"
mkdir -p "$(dirname "$LAST_SUCCESS_FILE")" "${WEB_ROOT}/graphs"

# Restore cached artifacts from persistent storage if present
if [ -d "${BACKUP_DIR}/graphs" ]; then
	if [ -n "$(ls -A "${BACKUP_DIR}/graphs" 2>/dev/null)" ]; then
		echo "Restoring graph artifacts from backup..."
		cp -a "${BACKUP_DIR}/graphs/." "${WEB_ROOT}/graphs/"
	fi
fi
if [ -f "${BACKUP_DIR}/last_success.json" ]; then
	echo "Restoring status file from backup..."
	cp -a "${BACKUP_DIR}/last_success.json" "${WEB_ROOT}/graphs/last_success.json"
	cp -a "${BACKUP_DIR}/last_success.json" "${LAST_SUCCESS_FILE}"
fi

# Copy index.html into RAM-based web root with station-specific URL
if [ -f /app/index.html ]; then
	SOURCE_STATION_NUMBER="${STATION_NUMBER:-030315}"
	SOURCE_URL="${GRAPH_URL:-https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation=${SOURCE_STATION_NUMBER}}"
	SOURCE_URL_ESCAPED=$(printf '%s' "$SOURCE_URL" | sed 's/[&/]/\\&/g')
	sed "s#https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation=[0-9]*#${SOURCE_URL_ESCAPED}#g" /app/index.html > "${WEB_ROOT}/index.html"
fi

echo "Starting graph downloader with internal scheduler..."
echo ""

# Run the Python script with explicit arguments
exec python3 /app/download_graph.py "${GRAPH_ARGS[@]}"

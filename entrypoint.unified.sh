#!/bin/bash
set -e

echo "Unified River Container Starting"
echo "==============================="
echo ""

echo "Configuration:"
echo "  LOG_LEVEL=${LOG_LEVEL:-INFO}"
echo "  FETCHER_INTERVAL_MINUTES=${FETCHER_INTERVAL_MINUTES:-10}"
echo "  GRAPH_INTERVAL_MINUTES=${GRAPH_INTERVAL_MINUTES:-120}"
echo "  BACKUP_INTERVAL_HOURS=${BACKUP_INTERVAL_HOURS:-24}"
echo "  SMART_ALERTS_ENABLED=${SMART_ALERTS_ENABLED:-true}"
echo "  SMART_ALERT_MATCH_WINDOW_HOURS=${SMART_ALERT_MATCH_WINDOW_HOURS:-4}"
echo "  SMART_ALERT_UPDATE_DELTA_M3S=${SMART_ALERT_UPDATE_DELTA_M3S:-30}"
echo "  SMART_ALERT_NEW_LOOKAHEAD_DAYS=${SMART_ALERT_NEW_LOOKAHEAD_DAYS:-1}"
echo "  STATION_NUMBER=${STATION_NUMBER:-030315}"
if [ -n "${HA_FORECAST_ENTITY_ID:-}" ]; then
  echo "  HA_FORECAST_ENTITY_ID=${HA_FORECAST_ENTITY_ID}"
fi
if [ -n "${HA_ALERTS_ENTITY_ID:-}" ]; then
  echo "  HA_ALERTS_ENTITY_ID=${HA_ALERTS_ENTITY_ID}"
fi
if [ -n "${HA_TOKEN:-}" ]; then
  echo "  HA_TOKEN=<set>"
fi
echo ""

# Prepare graph output directories.
WEB_ROOT="${WEB_ROOT:-/usr/share/nginx/html}"
LAST_SUCCESS_FILE="${LAST_SUCCESS_FILE:-/opt/graph_automation/last_success.json}"
BACKUP_DIR="${BACKUP_DIR:-/backup}"
mkdir -p "$(dirname "$LAST_SUCCESS_FILE")" "${WEB_ROOT}/graphs" "${BACKUP_DIR}"

# Restore cached artifacts from persistent storage if present.
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

# Build index.html with station-aware source URL.
if [ -f /app/graph/index.html ]; then
  SOURCE_STATION_NUMBER="${STATION_NUMBER:-030315}"
  SOURCE_URL="${GRAPH_URL:-https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation=${SOURCE_STATION_NUMBER}}"
  SOURCE_URL_ESCAPED=$(printf '%s' "$SOURCE_URL" | sed 's/[&/]/\\&/g')
  sed "s#https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation=[0-9]*#${SOURCE_URL_ESCAPED}#g" /app/graph/index.html > "${WEB_ROOT}/index.html"
fi

echo "Starting unified Python manager..."
echo ""

exec python3 /app/unified_entrypoint.py

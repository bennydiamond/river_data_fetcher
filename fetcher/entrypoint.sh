#!/bin/bash
set -e

echo "River Data Fetcher Container Starting"
echo "======================================="
echo ""

# Build CLI arguments from environment variables
FETCHER_ARGS=()
echo "Configuration:"
echo "  LOG_LEVEL=${LOG_LEVEL:-INFO}"
echo "  TZ=${TZ:-America/Montreal}"
echo "  FETCHER_INTERVAL_MINUTES=${FETCHER_INTERVAL_MINUTES:-10}"

if [ -n "${STATION_NUMBER:-}" ]; then
	echo "  STATION_NUMBER=${STATION_NUMBER}"
	FETCHER_ARGS+=("--station-number" "${STATION_NUMBER}")
fi
if [ -n "${HA_API_BASE_URL:-}" ]; then
	echo "  HA_API_BASE_URL=${HA_API_BASE_URL}"
	FETCHER_ARGS+=("--ha-api-base-url" "${HA_API_BASE_URL}")
fi
if [ -n "${DATA_URL:-}" ]; then
	echo "  DATA_URL=${DATA_URL}"
	FETCHER_ARGS+=("--data-url" "${DATA_URL}")
fi
if [ -n "${HA_TOKEN:-}" ]; then
	echo "  HA_TOKEN=<set>"
	FETCHER_ARGS+=("--ha-token" "${HA_TOKEN}")
fi
if [ -n "${STATION_NAME_PREFIX:-}" ]; then
	echo "  STATION_NAME_PREFIX=${STATION_NAME_PREFIX}"
	FETCHER_ARGS+=("--station-name-prefix" "${STATION_NAME_PREFIX}")
fi
if [ -n "${RIVER_NAME:-}" ]; then
	echo "  RIVER_NAME=${RIVER_NAME}"
	FETCHER_ARGS+=("--river-name" "${RIVER_NAME}")
fi
if [ -n "${RIVER_NAME_FALLBACK:-}" ]; then
	echo "  RIVER_NAME_FALLBACK=${RIVER_NAME_FALLBACK}"
	FETCHER_ARGS+=("--river-name-fallback" "${RIVER_NAME_FALLBACK}")
fi

echo ""
echo "Starting river data fetcher with internal scheduler..."
echo ""

# Run the Python script with explicit arguments
exec python3 /app/river_data_fetcher.py "${FETCHER_ARGS[@]}"

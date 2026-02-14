#!/bin/bash
set -e

WEB_ROOT="${WEB_ROOT:-/usr/share/nginx/html}"
BACKUP_DIR="${BACKUP_DIR:-/backup}"

mkdir -p "${BACKUP_DIR}/graphs"

if [ -d "${WEB_ROOT}/graphs" ]; then
  cp -a "${WEB_ROOT}/graphs/." "${BACKUP_DIR}/graphs/"
fi

if [ -f "${WEB_ROOT}/graphs/last_success.json" ]; then
  cp -a "${WEB_ROOT}/graphs/last_success.json" "${BACKUP_DIR}/last_success.json"
fi

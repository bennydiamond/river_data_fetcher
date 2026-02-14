# River Data Fetcher - Docker Setup

[![ci](https://github.com/bennydiamond/river_data_fetcher/actions/workflows/ci.yml/badge.svg)](https://github.com/bennydiamond/river_data_fetcher/actions/workflows/ci.yml)
[![build-and-publish](https://github.com/bennydiamond/river_data_fetcher/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/bennydiamond/river_data_fetcher/actions/workflows/docker-publish.yml)
[![Release](https://img.shields.io/github/v/release/bennydiamond/river_data_fetcher)](https://github.com/bennydiamond/river_data_fetcher/releases)
[![License](https://img.shields.io/github/license/bennydiamond/river_data_fetcher)](https://github.com/bennydiamond/river_data_fetcher/blob/master/LICENSE)

This project runs two automated tasks in separate Docker containers with configurable cron schedules:

1. **River Data Fetcher**: Fetches river data and sends to Home Assistant (default: every 10 minutes)
2. **Graph Downloader**: Downloads river graphs using Playwright (default: every 2 hours)

## Features

- ✅ Three lightweight, isolated containers
- ✅ Configurable cron schedules via environment variables
- ✅ Programs run immediately on startup, then follow cron schedule
- ✅ Home Assistant token configurable via environment variable or file
- ✅ Station number and HA API base URL configurable via env or CLI args
- ✅ Entity IDs derived from station number
- ✅ Automatic restarts on failure
- ✅ Montreal timezone configured
- ✅ **Remote syslog logging - minimal disk writes**
- ✅ **Configurable log levels (DEBUG, INFO, WARNING, ERROR)**
- ✅ **Nginx web server for index.html + graph images**
- ✅ **Daily backup of graph artifacts for offline fallback**
- ✅ **Stale-data detection with visible banner and image overlay**
- ✅ Optimized for Portainer deployment on ARM devices
- ✅ Resource limits for 2GB RAM systems

## Low-Write Storage Protection

**Optimized for Raspberry Pi and other low-power devices using flash storage:**

This setup is specifically designed to minimize writes on devices that use flash storage:

- 🛡️ **Minimal log files on device** - All logs sent to remote syslog server (UDP)
- 🛡️ **No temporary files** - Scripts output directly to stdout/stderr
- 🛡️ **Docker overlay optimized** - Only essential application data stored
- 🛡️ **Configurable verbosity** - Reduce logging to ERROR level in production
- 🛡️ **Graphs stored in tmpfs** - All generated files in RAM, minimal disk writes
- 🛡️ **State files in tmpfs** - Status tracking in RAM only
- 🛡️ **/tmp in tmpfs** - Playwright temp downloads and any temp files stay in RAM
- 🛡️ **Container caches in tmpfs** - Python caches and nginx temp files stay in RAM

**Result:** Minimal disk writes beyond Docker image layers. All runtime data is in RAM.

**After device reboot:**
- Cached graphs are restored from persistent backup if available
- `last_success.json` is restored to re-enable staleness checks
- A stale overlay is applied immediately if cached data is already too old

**Optional:** If you need persistent graphs beyond the daily backup, mount `/var/www/html/graphs` to NFS/network storage instead of tmpfs.

## Quick Start

### 1. Configure Home Assistant Token and Station

**Option A: .env File (Recommended)**

```bash
cp .env.example .env
# Edit .env and set your HA_TOKEN and station settings
nano .env
```

**Option B: Environment Variable in docker-compose.yml**

Edit `docker-compose.yml` and set the `HA_TOKEN` variable directly.

**Optional:** If you prefer a file, you can add a bind mount for `ha_token.txt`, but the default setup avoids any host file mounts to minimize disk writes.

### 2. (Optional) Customize Cron Schedules

Edit `.env` file or `docker-compose.yml` to adjust schedules.

```bash
docker-compose up -d --build
```

### 4. Build and Start Containers

```bash
# View fetcher logs
docker logs -f river-data-fetcher

# View graph downloader logs
docker logs -f graph-downloader
```

## Local Development (Build From Source)

Use the local override file to build images from the `fetcher/` and `graph/` folders.

```bash
# Build and run with local images
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Disable syslog during local testing (optional):

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.no-syslog.yml up -d --build
```

## Configuration

### Using .env File (Recommended)

1. Copy the example file:
```bash
cp .env.example .env
```

2. Edit `.env` and set your values. Refer to the template for more information on required/optional values to set:
[.env.example](.env.example)

### Cron Schedules

The schedules can be configured in `.env` file or directly in `docker-compose.yml`.

```yaml
# River Data Fetcher (default: every 10 minutes)
- FETCHER_CRON_SCHEDULE=*/10 * * * *

# Graph Downloader (default: every 2 hours at :05 - staggered)
- GRAPH_CRON_SCHEDULE=5 */2 * * *
```

**Cron Schedule Examples:**
- `*/5 * * * *` - Every 5 minutes
- `*/15 * * * *` - Every 15 minutes
- `0 * * * *` - Every hour
- `0 */3 * * *` - Every 3 hours
- `5 */3 * * *` - Every 3 hours at :05 (staggered)
- `0 0 * * *` - Every day at midnight

**💡 Tip for 2GB RAM systems:** Stagger schedules so containers don't run simultaneously (e.g., one at :00, another at :05)

### Timezone

Default is `America/Montreal`. Change in `.env` file or `docker-compose.yml`:
```bash
TZ=America/New_York
```

### Web Hosting (Nginx)

A lightweight Nginx container serves static content generated by the graph container:

- `index.html` at `http://<device-ip>:80/` (or `/index.html`)
- Graph images at `http://<device-ip>:80/graphs/`
- Latest graph: `http://<device-ip>:80/graphs/latest_graph.png`

Configure the port in `.env`:
```bash
WEB_PORT=80
```

**Note:** The web root (`/usr/share/nginx/html`) is a shared tmpfs volume, so all served content is RAM-only.
`index.html` is bind-mounted into the graph container and copied into tmpfs at startup, so you can edit it without rebuilding.
The graph downloader writes `last_success.json` into `/usr/share/nginx/html/graphs/` and the page displays it.

**Default storage policy:** No host bind mounts are used for runtime data. All writable paths are tmpfs to minimize disk writes.

### Logging

**Minimal Disk Writes:** Logs are sent directly to a remote syslog server to preserve flash storage lifespan.

Configure in `.env`:
```bash
# Logging configuration
LOG_LEVEL=INFO              # DEBUG, INFO, WARNING, ERROR
SYSLOG_HOST=192.168.0.15   # Your syslog server IP
SYSLOG_PORT=514             # UDP port
```

**Log Levels:**
- `ERROR` - Only errors (minimal, recommended for production)
- `WARNING` - Errors + warnings
- `INFO` - Normal operation logs (default)
- `DEBUG` - Verbose output for troubleshooting

**Viewing Logs:**
- On your syslog server: Filter by tags `river-data-fetcher`, `graph-downloader`, or `river-web`

**Note:** By default, logs are NOT stored on the device to protect flash storage.

**Disable remote syslog (optional):** If you do not have a syslog server, use the override file to disable container logging entirely (no `docker logs` output):

```bash
docker-compose -f docker-compose.yml -f docker-compose.no-syslog.yml up -d --build
```

**Portainer:** When creating the stack, add both compose files so the override is applied:
- `docker-compose.yml`
- `docker-compose.no-syslog.yml`

### Retries and Backups

Both scripts retry network failures by default:

```bash
FETCH_RETRY_COUNT=3
FETCH_RETRY_DELAY_SECONDS=5
```

### Home Assistant Entity IDs

Entity IDs are derived from the station number to keep configuration minimal:

```text
sensor.station_<station_number>_flow_rate
sensor.station_<station_number>_height_level
```

The graph downloader also maintains a daily backup on persistent storage so the web server can serve data after a reboot without connectivity:

```bash
GRAPH_BACKUP_CRON_SCHEDULE=15 3 * * *
BACKUP_DIR=/backup
```

Backup behavior:
- If the backup is empty, the first successful download triggers an immediate backup.
- On startup, cached graphs and `last_success.json` are restored into RAM.
- If cached data is stale, the warning overlay is applied immediately.

## Portainer Deployment

### Using Portainer Stacks

1. In Portainer, go to **Stacks** → **Add Stack**
2. Name it: `river-data-fetcher`
3. Choose **Git Repository** or **Upload** your files
4. Deploy the stack

### Using Portainer Custom Templates

1. Copy `docker-compose.yml` content
2. Go to **App Templates** → **Add Custom Template**
3. Paste the content and deploy


## Container Details

### River Data Fetcher Container
- **Base Image**: `python:3.11-slim`
- **Size**: ~150MB
- **Memory Limit**: 128MB (64MB reserved)
- **CPU Limit**: 0.5 cores
- **Schedule**: Every 10 minutes (configurable)
- **Dependencies**: requests, beautifulsoup4, pytz

### Graph Downloader Container
- **Base Image**: `mcr.microsoft.com/playwright/python:v1.58.0-jammy`
- **Size**: ~1.5GB (includes Chrome browser)
- **Memory Limit**: 768MB (512MB reserved)
- **CPU Limit**: 1.0 core
- **Shared Memory**: 256MB
- **Schedule**: Every 2 hours at :05 (configurable)
- **Dependencies**: playwright, pillow

**Note:** Resource limits are optimized for 2GB RAM systems. Adjust in `docker-compose.yml` if you have more RAM available.


### View logs

**If using syslog (default):**
Check your syslog server at `192.168.0.15` (or configured IP) for logs tagged with:
- `river-data-fetcher`
- `graph-downloader`

**For debugging, temporarily switch to local logging:**
Edit `docker-compose.yml` and comment out the `logging:` sections, then:
```bash
docker-compose down && docker-compose up -d
docker logs -f river-data-fetcher
```

**Enable debug logging:**
```bash
# In .env file
LOG_LEVEL=DEBUG
# Then restart
docker-compose restart
```

### Restart containers
```bash
docker-compose restart
```

### Check cron schedule inside container
```bash
docker exec river-data-fetcher crontab -l
docker exec graph-downloader crontab -l
```

### Run script manually
```bash
# Run fetcher manually
docker exec river-data-fetcher python3 /app/river_data_fetcher.py

# Run graph downloader manually
docker exec graph-downloader python3 /app/download_graph.py
```

### Rebuild after changes
```bash
docker-compose down
docker-compose up -d --build
```

## Monitoring in Portainer

1. **Container Status**: Check if containers are running (green)
2. **Logs**: View real-time logs for each container
3. **Stats**: Monitor CPU and memory usage
4. **Quick Actions**: 
   - Restart containers
   - View/edit environment variables
   - Access container console

## Updates

To update the scripts without rebuilding:

1. Edit the Python files
2. Restart containers:
```bash
docker-compose restart
```

To update dependencies or configurations:
```bash
docker-compose down
docker-compose up -d --build
```

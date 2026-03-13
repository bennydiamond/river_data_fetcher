import argparse
import asyncio
from playwright.async_api import async_playwright
import os
import io
import logging
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import json
import subprocess
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
import csv
import requests  # Added for HA API push

# --- CONFIGURATION ---
DEFAULT_STATION_NUMBER = "030315"
DEFAULT_GRAPH_URL_TEMPLATE = (
    "https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation={station_number}"
)
DEFAULT_HA_API_BASE_URL = "http://192.168.0.250:8123/api"
DEFAULT_HA_ENTITY_ID = "sensor.riviere_noire_flood_forecast"

LAST_SUCCESS_FILE = os.environ.get(
    "LAST_SUCCESS_FILE", "/opt/graph_automation/last_success.json"
)
OUTPUT_DIR = "/usr/share/nginx/html/graphs"
LAST_SUCCESS_WEB_FILE = os.path.join(OUTPUT_DIR, "last_success.json")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backup")
BACKUP_SCRIPT = os.environ.get("BACKUP_SCRIPT", "/app/backup_web_root.sh")

WARNING_THRESHOLD_HOURS = 12
WARNING_TEXT = "DONNÉES PÉRIMÉES ET IMPRÉCISES"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TIMEOUT_MS = 60000  # 60 seconds
FETCH_RETRY_COUNT = int(os.environ.get("FETCH_RETRY_COUNT", "3"))
FETCH_RETRY_DELAY_SECONDS = int(os.environ.get("FETCH_RETRY_DELAY_SECONDS", "10"))

# Flood Prediction Settings
FLOW_WARNING_THRESHOLD = float(
    os.environ.get("PREDICTION_THRESHOLD_M3S", "100.0")
)  # m³/s

# Font & Text Settings
FONT_SIZE_BASE_RATIO_WIDTH = 0.05
WARNING_TEXT_MAX_WIDTH_RATIO = 0.90
TEXT_COLOR = (255, 0, 0)

# Image processing dimensions
TARGET_MAX_WIDTH = 720
TARGET_MAX_HEIGHT = 437
CROP_BOTTOM_PIXELS = 40
JPEG_QUALITY = 75
PNG_PALETTE_COLORS = 256

# --- LOGGING SETUP ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] graph_downloader: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Quebec Timezone
QUEBEC_TZ = pytz.timezone("America/Montreal")


# --- HA TOKEN LOADER ---
def load_ha_token():
    token = os.environ.get("HA_TOKEN")
    if token:
        logger.info("Using HA token from environment variable")
        return token.strip()

    try:
        script_dir = os.path.dirname(__file__)
        token_file_path = os.path.join(script_dir, "ha_token.txt")
        root_token_file_path = os.path.abspath(
            os.path.join(script_dir, "..", "ha_token.txt")
        )
        for candidate_path in (token_file_path, root_token_file_path):
            if not os.path.exists(candidate_path):
                continue
            with open(candidate_path, "r") as f:
                token = f.read().strip()
                if not token:
                    raise ValueError("Token file is empty.")
                logger.info(f"Using HA token from file: {candidate_path}")
                return token
    except Exception as e:
        logger.error(f"Error reading HA token: {e}")
        exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download river graph, predictions, and push to HA"
    )
    parser.add_argument(
        "--station-number",
        default=os.environ.get("STATION_NUMBER", DEFAULT_STATION_NUMBER),
    )
    parser.add_argument("--graph-url", default=os.environ.get("GRAPH_URL"))
    parser.add_argument(
        "--ha-api-base-url",
        default=os.environ.get("HA_API_BASE_URL", DEFAULT_HA_API_BASE_URL),
    )
    parser.add_argument("--ha-token", default=os.environ.get("HA_TOKEN"))
    parser.add_argument("--check-stale", action="store_true")
    return parser.parse_args()


def build_runtime_config(args):
    station_number = args.station_number.strip()
    graph_url = args.graph_url
    if not graph_url:
        graph_url = DEFAULT_GRAPH_URL_TEMPLATE.format(station_number=station_number)
    return {
        "station_number": station_number,
        "graph_url": graph_url,
        "ha_api_base_url": args.ha_api_base_url.strip(),
        "ha_entity_id": DEFAULT_HA_ENTITY_ID,
    }


# --- HELPER FUNCTIONS ---
def load_status_data():
    if os.path.exists(LAST_SUCCESS_FILE):
        try:
            with open(LAST_SUCCESS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading status file: {e}")
    return {}


def save_last_success_time():
    os.makedirs(os.path.dirname(LAST_SUCCESS_FILE), exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    data = {
        "last_successful_run": timestamp,
        "timezone": "America/Montreal",
        "stale_applied": False,
    }
    with open(LAST_SUCCESS_FILE, "w") as f:
        json.dump(data, f)
    with open(LAST_SUCCESS_WEB_FILE, "w") as f:
        json.dump(data, f)
    logger.info("Success timestamp updated.")


def mark_stale_applied():
    data = load_status_data()
    if not data.get("last_successful_run"):
        return
    data["stale_applied"] = True
    with open(LAST_SUCCESS_FILE, "w") as f:
        json.dump(data, f)
    with open(LAST_SUCCESS_WEB_FILE, "w") as f:
        json.dump(data, f)


def add_warning_overlay(img_path, warning_text):
    """Only used if the download FAILS, to stamp the old image on disk."""
    try:
        if not os.path.exists(img_path):
            return
        logger.warning(f"Applying warning overlay to {img_path}...")
        img_obj = Image.open(img_path)
        draw = ImageDraw.Draw(img_obj)

        current_font_size = int(img_obj.width * FONT_SIZE_BASE_RATIO_WIDTH)
        font = None
        while current_font_size > 5:
            try:
                font = ImageFont.truetype(FONT_PATH, current_font_size)
            except IOError:
                font = ImageFont.load_default()
            try:
                text_bbox = draw.textbbox((0, 0), warning_text, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
            except AttributeError:
                text_width, text_height = draw.textsize(warning_text, font=font)
            if text_width <= img_obj.width * WARNING_TEXT_MAX_WIDTH_RATIO:
                break
            current_font_size -= 2

        if font is None:
            font = ImageFont.load_default()
        x = (img_obj.width - text_width) / 2
        y = int(img_obj.height * 0.02)
        padding = 10
        overlay = Image.new("RGBA", img_obj.size, (255, 255, 255, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [
                max(0, x - padding),
                max(0, y - padding),
                min(img_obj.width, x + text_width + padding),
                min(img_obj.height, y + text_height + padding),
            ],
            fill=(0, 0, 0, 128),
        )
        img_obj.paste(overlay, (0, 0), overlay)
        stroke_width = 2
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), warning_text, font=font, fill=(0, 0, 0))
        draw.text((x, y), warning_text, font=font, fill=TEXT_COLOR)
        img_obj.save(img_path)
        logger.info("Warning overlay saved successfully.")
    except Exception as e:
        logger.error(f"Failed to add overlay: {e}")


def process_and_save_image(image_data_buffer):
    """
    Accepts a BytesIO buffer (RAM) instead of a file path.
    Saves only the target artifacts to disk.
    """
    try:
        # Load directly from memory
        img = Image.open(image_data_buffer)

        # 1. Crop
        cropped_img = img.crop((0, 0, img.width, img.height - CROP_BOTTOM_PIXELS))
        cropped_filename = "latest_graph_cropped.png"
        cropped_path = os.path.join(OUTPUT_DIR, cropped_filename)
        cropped_img.save(cropped_path)
        logger.info(f"Processed & Saved: {cropped_path}")

        # 2. Resize & Compress
        compressed_path = os.path.join(OUTPUT_DIR, "latest_graph_compressed.png")
        jpg_path = os.path.join(OUTPUT_DIR, "latest_graph.jpg")
        target_width, target_height = TARGET_MAX_WIDTH, TARGET_MAX_HEIGHT
        scale = min(
            target_width / cropped_img.width, target_height / cropped_img.height
        )
        new_size = (int(cropped_img.width * scale), int(cropped_img.height * scale))
        resized_img = cropped_img.resize(new_size, Image.Resampling.LANCZOS)

        paletted_img = resized_img.quantize(
            colors=PNG_PALETTE_COLORS, method=Image.Quantize.MEDIANCUT
        )
        paletted_img.save(compressed_path, "PNG", optimize=True)
        resized_img.save(jpg_path, "JPEG", quality=JPEG_QUALITY)

        # 3. Symlink
        symlink_path = os.path.join(OUTPUT_DIR, "latest_graph.png")
        if os.path.exists(symlink_path) or os.path.islink(symlink_path):
            os.remove(symlink_path)
        os.symlink(cropped_filename, symlink_path)
        logger.info("Symlinks updated.")
        return True
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        return False


def check_and_overlay_stale_data():
    """Backup method: Only runs if the download failed."""
    data = load_status_data()
    last_success_raw = data.get("last_successful_run")
    if not last_success_raw:
        return
    if data.get("stale_applied") is True:
        logger.info("Stale overlay already applied. Skipping.")
        return
    last_success = datetime.fromisoformat(last_success_raw)
    time_since = datetime.now(last_success.tzinfo) - last_success
    if time_since > timedelta(hours=WARNING_THRESHOLD_HOURS):
        logger.warning(
            f"Download failed. Data is {time_since} old. Overlaying old image."
        )
        add_warning_overlay(
            os.path.join(OUTPUT_DIR, "latest_graph_cropped.png"), WARNING_TEXT
        )
        mark_stale_applied()


def backup_if_missing():
    if not os.path.isdir(BACKUP_DIR):
        return
    backup_graphs = os.path.join(BACKUP_DIR, "graphs")
    if os.path.isdir(backup_graphs) and os.listdir(backup_graphs):
        return
    if os.path.isfile(BACKUP_SCRIPT):
        try:
            subprocess.run([BACKUP_SCRIPT], check=True)
            logger.info("Initial backup completed.")
        except Exception as e:
            logger.warning(f"Initial backup failed: {e}")


def send_forecast_to_home_assistant(forecast_data, runtime_config, ha_headers):
    """Pushes the forecast payload to Home Assistant."""
    if not forecast_data:
        return

    # Prepare HA payload with event_count as the primary state
    payload = {
        "state": forecast_data["event_count"],
        "attributes": {
            "friendly_name": "Prévisions Rivière Noire",
            "icon": "mdi:alert-water",
            "state_class": "measurement",
            "max_predicted_flow": forecast_data["max_predicted_flow"],
            "max_predicted_flow_unit": "m³/s",
            "events": forecast_data["events"],
            "critical_date": forecast_data["critical_date"],
            "threshold_m3s": forecast_data["threshold_m3s"],
            "csv_source_url": forecast_data.get("csv_source_url"),
            "last_updated": forecast_data["timestamp"],
        },
    }

    ha_url = (
        f"{runtime_config['ha_api_base_url']}/states/{runtime_config['ha_entity_id']}"
    )

    logger.debug(
        f"Sending forecast data to Home Assistant: {runtime_config['ha_entity_id']}"
    )
    try:
        response = requests.post(ha_url, json=payload, headers=ha_headers, timeout=10)
        response.raise_for_status()
        logger.info(
            f"Forecast successfully pushed to HA. Status: {response.status_code}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending forecast to Home Assistant: {e}")


def process_csv_prediction(csv_buffer, csv_source_url=None):
    """Parses the CSV, extracts events, saves locally, and returns the payload dict."""
    csv_buffer.seek(0)
    text_data = csv_buffer.read().decode("latin-1", errors="replace")
    reader = csv.reader(text_data.splitlines())
    next(reader, None)

    raw_events = []
    current_event = None
    CEHQ_TZ = pytz.timezone("EST")
    now_utc = datetime.now(pytz.utc)
    now_local = datetime.now(QUEBEC_TZ)

    def parse_float(val_str):
        try:
            return float(val_str.strip())
        except ValueError:
            return None

    for row in reader:
        if len(row) < 8:
            continue
        date_str = row[0].strip()
        try:
            row_time_aware = CEHQ_TZ.localize(
                datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            )
        except ValueError:
            continue

        if row_time_aware < now_utc:
            continue

        flow_vals = [
            parse_float(row[idx]) for idx in [4, 5] if parse_float(row[idx]) is not None
        ]
        row_max = max(flow_vals) if flow_vals else 0.0

        ic_low = parse_float(row[6])
        ic_high = parse_float(row[7])

        if row_max >= FLOW_WARNING_THRESHOLD:
            if current_event is None:
                current_event = {
                    "start_time": date_str,
                    "peak_flow": row_max,
                    "peak_time": date_str,
                    "ic_low_at_peak": ic_low,
                    "ic_high_at_peak": ic_high,
                }
            else:
                if row_max > current_event["peak_flow"]:
                    current_event.update(
                        {
                            "peak_flow": row_max,
                            "peak_time": date_str,
                            "ic_low_at_peak": ic_low,
                            "ic_high_at_peak": ic_high,
                        }
                    )
        else:
            if current_event is not None:
                current_event["end_time"] = date_str
                raw_events.append(current_event)
                current_event = None

    if current_event is not None:
        current_event["end_time"] = "Fin des prévisions"
        raw_events.append(current_event)

    # Merge events < 4 hours apart
    merged_events = []
    for ev in raw_events:
        if not merged_events:
            merged_events.append(ev)
            continue
        last_ev = merged_events[-1]
        if last_ev["end_time"] == "Fin des prévisions":
            merged_events.append(ev)
            continue
        try:
            end_t = CEHQ_TZ.localize(
                datetime.strptime(last_ev["end_time"], "%Y-%m-%d %H:%M:%S")
            )
            start_t = CEHQ_TZ.localize(
                datetime.strptime(ev["start_time"], "%Y-%m-%d %H:%M:%S")
            )
            if (start_t - end_t) <= timedelta(hours=4):
                if ev["peak_flow"] > last_ev["peak_flow"]:
                    last_ev.update(
                        {
                            "peak_flow": ev["peak_flow"],
                            "peak_time": ev["peak_time"],
                            "ic_low_at_peak": ev["ic_low_at_peak"],
                            "ic_high_at_peak": ev["ic_high_at_peak"],
                        }
                    )
                last_ev["end_time"] = ev["end_time"]
            else:
                merged_events.append(ev)
        except ValueError:
            merged_events.append(ev)

    absolute_max = max((ev["peak_flow"] for ev in merged_events), default=0.0)
    critical_date = next(
        (ev["peak_time"] for ev in merged_events if ev["peak_flow"] == absolute_max),
        None,
    )

    payload = {
        "high_flow_predicted": len(merged_events) > 0,
        "max_predicted_flow": absolute_max,
        "critical_date": critical_date,
        "event_count": len(merged_events),
        "events": merged_events,
        "threshold_m3s": FLOW_WARNING_THRESHOLD,
        "csv_source_url": csv_source_url,
        "timestamp": now_local.isoformat(),
    }

    # Save locally as a backup / web asset
    output_file = os.path.join(OUTPUT_DIR, "flood_prediction.json")
    with open(output_file, "w") as f:
        json.dump(payload, f, indent=2)

    return payload


# --- MAIN PLAYWRIGHT FETCH ROUTINE ---
async def download_graph_png(runtime_config, ha_headers):
    url = runtime_config["graph_url"]
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for attempt in range(1, FETCH_RETRY_COUNT + 1):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()

                # User Agent Spoofing
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )

                page = await context.new_page()
                page.set_default_timeout(TIMEOUT_MS)

                logger.info(f"Navigating to {url}...")

                await page.goto(url)
                await page.wait_for_selector("#container3", state="visible")
                logger.info("Graph container visible.")

                # --- 1. DOWNLOAD PNG ---
                menu_btn_sel = 'button.highcharts-a11y-proxy-element[aria-label*="Détail des prochains jours"]'
                menu_button = await page.wait_for_selector(
                    menu_btn_sel, state="visible"
                )

                await page.wait_for_timeout(1000)
                await menu_button.click()
                logger.info("Menu clicked.")

                dl_text = "Télécharger l'image PNG"
                await page.wait_for_selector(f"text={dl_text}")

                await page.wait_for_timeout(1000)

                async with page.expect_download(timeout=TIMEOUT_MS) as download_info:
                    logger.info(f"Clicking '{dl_text}'...")
                    await page.click(f"text={dl_text}", force=True)

                download = await download_info.value

                # --- IN-MEMORY PROCESSING ---
                # Playwright saves to a system temp file first (e.g., /tmp/...).
                # We read that directly into RAM. Playwright cleans it up automatically on browser.close().
                temp_path = await download.path()
                logger.info(
                    f"Download acquired (temp path: {temp_path}). Loading into RAM..."
                )

                with open(temp_path, "rb") as f:
                    memory_buffer = io.BytesIO(f.read())

                # Pass the RAM buffer to the processor
                if process_and_save_image(memory_buffer):
                    save_last_success_time()
                    backup_if_missing()

                # --- 2. DOWNLOAD CSV ---
                logger.info("Re-opening menu to download CSV...")
                # The menu closes after the first click, so we open it again
                await menu_button.click()

                csv_text = "Télécharger en CSV"
                await page.wait_for_selector(f"text={csv_text}", state="visible")
                await page.wait_for_timeout(1000)

                async with page.expect_download(timeout=TIMEOUT_MS) as csv_info:
                    logger.info(f"Clicking '{csv_text}'...")
                    await page.click(f"text={csv_text}", force=True)

                csv_download = await csv_info.value
                csv_temp_path = await csv_download.path()
                csv_source_url = csv_download.url

                logger.info("CSV downloaded. Parsing prediction data...")
                with open(csv_temp_path, "rb") as f:
                    csv_buffer = io.BytesIO(f.read())

                # Process the CSV and get the payload dictionary back
                forecast_payload = process_csv_prediction(
                    csv_buffer,
                    csv_source_url=csv_source_url or runtime_config["graph_url"],
                )

                # Push the dictionary directly to Home Assistant
                send_forecast_to_home_assistant(
                    forecast_payload, runtime_config, ha_headers
                )

                await browser.close()
                return

        except Exception as e:
            if attempt < FETCH_RETRY_COUNT:
                logger.warning(
                    "Operation failed (attempt %s/%s): %s. Retrying in %ss...",
                    attempt,
                    FETCH_RETRY_COUNT,
                    e,
                    FETCH_RETRY_DELAY_SECONDS,
                )
                try:
                    await asyncio.sleep(FETCH_RETRY_DELAY_SECONDS)
                except Exception:
                    pass
            else:
                logger.error(
                    "Operation failed after %s attempts: %s",
                    FETCH_RETRY_COUNT,
                    e,
                    exc_info=True,
                )
                check_and_overlay_stale_data()


if __name__ == "__main__":
    args = parse_args()
    runtime_config = build_runtime_config(args)

    # Initialize HA headers once on startup
    ha_long_lived_token = args.ha_token or load_ha_token()
    ha_headers = {
        "Authorization": f"Bearer {ha_long_lived_token}",
        "Content-Type": "application/json",
    }

    if args.check_stale:
        logger.info("--- Checking for stale cached data ---")
        check_and_overlay_stale_data()
        logger.info("--- Stale check finished ---")
    else:
        logger.info("Starting graph downloader with embedded scheduler")

        # Get intervals from env (in minutes, defaults match original cron)
        graph_interval_minutes = int(
            os.environ.get("GRAPH_INTERVAL_MINUTES", "120")
        )  # 2 hours
        backup_interval_hours = int(
            os.environ.get("BACKUP_INTERVAL_HOURS", "24")
        )  # daily

        # Set up scheduler with Quebec timezone
        scheduler = BlockingScheduler(timezone=QUEBEC_TZ)

        # Schedule graph download
        scheduler.add_job(
            lambda: asyncio.run(download_graph_png(runtime_config, ha_headers)),
            "interval",
            minutes=graph_interval_minutes,
            id="graph_download_job",
        )

        # Schedule backup
        scheduler.add_job(
            lambda: subprocess.run([BACKUP_SCRIPT], check=False),
            "interval",
            hours=backup_interval_hours,
            id="backup_job",
        )

        logger.info(
            f"Scheduled graph/csv download every {graph_interval_minutes} minutes"
        )
        logger.info(f"Scheduled backup every {backup_interval_hours} hours")
        logger.info("Scheduler starting...")

        # Run initial setup
        logger.info("Restoring from backup if available...")
        backup_if_missing()
        check_and_overlay_stale_data()

        logger.info("Running initial graph and csv download...")
        asyncio.run(download_graph_png(runtime_config, ha_headers))

        # Start scheduler (blocking)
        scheduler.start()

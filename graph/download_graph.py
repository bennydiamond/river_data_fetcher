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

# --- CONFIGURATION ---
DEFAULT_STATION_NUMBER = "030315"
DEFAULT_GRAPH_URL_TEMPLATE = (
    "https://www.cehq.gouv.qc.ca/suivihydro/graphique.asp?noStation={station_number}"
)
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download river graph and publish artifacts"
    )
    parser.add_argument(
        "--station-number",
        default=os.environ.get("STATION_NUMBER", DEFAULT_STATION_NUMBER),
        help="Station number (env: STATION_NUMBER)",
    )
    parser.add_argument(
        "--graph-url",
        default=os.environ.get("GRAPH_URL"),
        help="Override graph URL (env: GRAPH_URL)",
    )
    parser.add_argument(
        "--check-stale",
        action="store_true",
        help="Check and overlay stale data without downloading",
    )
    return parser.parse_args()


def build_runtime_config(args):
    station_number = args.station_number.strip()
    graph_url = args.graph_url
    if not graph_url:
        graph_url = DEFAULT_GRAPH_URL_TEMPLATE.format(station_number=station_number)
    return {
        "station_number": station_number,
        "graph_url": graph_url,
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
    tz_name = os.environ.get("TZ", "America/Montreal")
    os.makedirs(os.path.dirname(LAST_SUCCESS_FILE), exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    data = {
        "last_successful_run": timestamp,
        "timezone": tz_name,
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
                if font == ImageFont.load_default():
                    break
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


# --- MAIN FUNCTION ---
async def download_graph_png(graph_url):
    url = graph_url
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

                # Menu Interaction
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

    if args.check_stale:
        logger.info("--- Checking for stale cached data ---")
        check_and_overlay_stale_data()
        logger.info("--- Stale check finished ---")
    else:
        logger.info("--- Starting graph download script ---")
        asyncio.run(download_graph_png(runtime_config["graph_url"]))
        logger.info("--- Script finished ---")

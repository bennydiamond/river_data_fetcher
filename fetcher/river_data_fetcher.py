import argparse
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os
import re
import logging
from apscheduler.schedulers.blocking import BlockingScheduler

# --- LOGGING CONFIGURATION ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] river_data_fetcher: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
DEFAULT_STATION_NUMBER = "030315"
DEFAULT_STATION_NAME_PREFIX = "Upton"
DEFAULT_RIVER_NAME_FALLBACK = "Noire"
DEFAULT_DATA_URL_TEMPLATE = (
    "https://www.cehq.gouv.qc.ca/suivihydro/tableau.asp?NoStation={station_number}"
)
DEFAULT_HA_API_BASE_URL = "http://192.168.0.250:8123/api"
FETCH_RETRY_COUNT = int(os.environ.get("FETCH_RETRY_COUNT", "3"))
FETCH_RETRY_DELAY_SECONDS = int(os.environ.get("FETCH_RETRY_DELAY_SECONDS", "5"))

# Quebec Timezone (for local timestamps)
QUEBEC_TZ = pytz.timezone("America/Montreal")


# --- Helper function to load the token ---
def load_ha_token():
    # Try environment variable first
    token = os.environ.get("HA_TOKEN")
    if token:
        logger.info("Using HA token from environment variable")
        return token.strip()

    # Fall back to file
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
    except FileNotFoundError:
        logger.error(
            f"Home Assistant token not found in environment variable HA_TOKEN or file at '{token_file_path}'."
        )
        logger.error(
            "Please set HA_TOKEN environment variable or create the file and paste your long-lived access token inside."
        )
        exit(1)
    except ValueError as e:
        logger.error(f"Error reading token from file: {e}")
        logger.error("Please ensure the token file contains a valid token.")
        exit(1)
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading token: {e}")
        exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch river data and send it to Home Assistant"
    )
    parser.add_argument(
        "--station-number",
        default=os.environ.get("STATION_NUMBER", DEFAULT_STATION_NUMBER),
        help="Station number (env: STATION_NUMBER)",
    )
    parser.add_argument(
        "--ha-api-base-url",
        default=os.environ.get("HA_API_BASE_URL", DEFAULT_HA_API_BASE_URL),
        help="Home Assistant API base URL (env: HA_API_BASE_URL)",
    )
    parser.add_argument(
        "--data-url",
        default=os.environ.get("DATA_URL"),
        help="Override data URL (env: DATA_URL)",
    )
    parser.add_argument(
        "--ha-token",
        default=os.environ.get("HA_TOKEN"),
        help="Home Assistant long-lived access token (env: HA_TOKEN)",
    )
    parser.add_argument(
        "--station-name-prefix",
        default=os.environ.get("STATION_NAME_PREFIX", DEFAULT_STATION_NAME_PREFIX),
        help="Station name prefix (env: STATION_NAME_PREFIX)",
    )
    parser.add_argument(
        "--river-name",
        default=os.environ.get("RIVER_NAME"),
        help="Override river name in station display (env: RIVER_NAME)",
    )
    parser.add_argument(
        "--river-name-fallback",
        default=os.environ.get("RIVER_NAME_FALLBACK", DEFAULT_RIVER_NAME_FALLBACK),
        help="Fallback river name (env: RIVER_NAME_FALLBACK)",
    )
    return parser.parse_args()


def build_runtime_config(args):
    station_number = args.station_number.strip()
    data_url = args.data_url
    if not data_url:
        data_url = DEFAULT_DATA_URL_TEMPLATE.format(station_number=station_number)
    ha_api_base_url = args.ha_api_base_url.strip()
    station_name_prefix = args.station_name_prefix.strip()
    river_name_override = args.river_name.strip() if args.river_name else ""
    river_name_fallback = args.river_name_fallback.strip()
    ha_flow_entity_id = f"sensor.station_{station_number}_flow_rate"
    ha_height_entity_id = f"sensor.station_{station_number}_height_level"
    return {
        "station_number": station_number,
        "data_url": data_url,
        "ha_api_base_url": ha_api_base_url,
        "station_name_prefix": station_name_prefix,
        "river_name_override": river_name_override,
        "river_name_fallback": river_name_fallback,
        "ha_flow_entity_id": ha_flow_entity_id,
        "ha_height_entity_id": ha_height_entity_id,
    }


# --- HELPER FUNCTION FOR DATA FETCHING AND PARSING ---
def fetch_and_parse_data(
    data_url,
    station_number,
    station_name_prefix,
    river_name_override,
    river_name_fallback,
):
    """Fetches the HTML, parses the table, and returns the latest data."""
    logger.debug(f"Fetching data from {data_url}...")
    response = None
    for attempt in range(1, FETCH_RETRY_COUNT + 1):
        try:
            response = requests.get(data_url, timeout=15)
            response.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            if attempt < FETCH_RETRY_COUNT:
                logger.warning(
                    "Error fetching data (attempt %s/%s): %s. Retrying in %ss...",
                    attempt,
                    FETCH_RETRY_COUNT,
                    e,
                    FETCH_RETRY_DELAY_SECONDS,
                )
                try:
                    import time

                    time.sleep(FETCH_RETRY_DELAY_SECONDS)
                except Exception:
                    pass
            else:
                logger.error(
                    "Error fetching data after %s attempts: %s", FETCH_RETRY_COUNT, e
                )
                return None

    # Explicitly set encoding to UTF-8
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")

    data_table = None
    header_row = None

    tables = soup.find_all("table")

    for table in tables:
        potential_header_rows = table.find_all("tr", recursive=False)
        for row in potential_header_rows:
            td_elements = row.find_all("td")
            if len(td_elements) >= 4:
                header_texts_from_font_tags = []
                for td_idx in [0, 1, 2, 3]:
                    font_tag = td_elements[td_idx].find("font")
                    if font_tag:
                        text_content = font_tag.get_text(strip=True).replace(
                            "\xa0", " "
                        )
                        header_texts_from_font_tags.append(text_content)
                    else:
                        header_texts_from_font_tags.append("NO FONT TAG FOUND")

                if (
                    "Date" in header_texts_from_font_tags[0]
                    and "Heure" in header_texts_from_font_tags[1]
                    and "Niveau" in header_texts_from_font_tags[2]
                    and "Débit" in header_texts_from_font_tags[3]
                ):
                    header_row = row
                    data_table = table
                    break
            else:
                pass  # Row has less than 4 cells, skipping header check

        if data_table:
            break

    if not data_table or not header_row:
        logger.error(
            "Could not find the main data table or its header row with expected column headers."
        )
        return None

    data_rows = data_table.find_all("tr")[
        data_table.find_all("tr").index(header_row) + 1 :
    ]

    if not data_rows:
        logger.error("No data rows found in the table after skipping header.")
        return None

    # Extract the latest (first) data row as requested
    latest_row = data_rows[0]
    cells = latest_row.find_all("td")

    if len(cells) < 4:
        logger.error(
            f"Not enough cells found in the latest row. Expected at least 4, got {len(cells)}"
        )
        return None

    try:
        date_str = cells[0].text.strip().replace("\xa0", "")
        time_str = cells[1].text.strip().replace("\xa0", "")

        height_str = cells[2].text.replace("\xa0", "").strip()
        height_str = re.sub(r"[^0-9,\.]", "", height_str)
        if height_str.count(",") == 1 and height_str.count(".") == 0:
            height_str = height_str.replace(",", ".")

        flow_str = cells[3].text.replace("\xa0", "").strip()
        flow_str = re.sub(r"[^0-9,\.]", "", flow_str)
        if flow_str.count(",") == 1 and flow_str.count(".") == 0:
            flow_str = flow_str.replace(",", ".")

        quebec_tz = pytz.timezone("America/Montreal")

        datetime_naive = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
        )
        datetime_aware_local_quebec = quebec_tz.localize(datetime_naive)

        datetime_utc = datetime_aware_local_quebec.astimezone(pytz.utc)

        # Extract station ID
        station_name_tag = soup.find("span", id="spnNoStation")
        station_id = (
            station_name_tag.text.strip() if station_name_tag else station_number
        )

        # Construct station_name as "<Prefix> - <Station> - <River>"
        if river_name_override:
            river_designation = river_name_override
        else:
            river_designation = river_name_fallback
            station_name_full_text_element = soup.find(
                "p",
                align="center",
                class_=None,
                string=lambda s: "Niveau d'eau et débit à la station" in s,
            )
            if station_name_full_text_element:
                full_text = station_name_full_text_element.get_text(strip=True)
                parts_after_id = full_text.split(station_id)
                if len(parts_after_id) > 1:
                    potential_river_part = parts_after_id[1].strip()
                    if " - " in potential_river_part:
                        river_designation = potential_river_part.split(" - ")[
                            -1
                        ].strip()

        if station_name_prefix:
            station_name = f"{station_name_prefix} - {station_id} - {river_designation}"
        else:
            station_name = f"{station_id} - {river_designation}"

        height_unit = ""
        flow_unit = ""

        header_tds_for_units = header_row.find_all("td")

        # Height Unit
        if len(header_tds_for_units) > 2:
            height_font_tag = header_tds_for_units[2].find("font")
            if height_font_tag:
                height_unit_raw = height_font_tag.get_text(strip=True).replace(
                    "\xa0", " "
                )
                if "(m)" in height_unit_raw or "m" == height_unit_raw.lower():
                    height_unit = "m"
                elif "m" in height_unit_raw and "Niveau" in height_unit_raw:
                    height_unit = "m"

        # Flow Unit
        if len(header_tds_for_units) > 3:
            flow_font_tag = header_tds_for_units[3].find("font")
            if flow_font_tag:
                flow_unit_raw = flow_font_tag.get_text(strip=True).replace("\xa0", " ")
                if "(m³/s)" in flow_unit_raw or "m³/s" == flow_unit_raw:
                    flow_unit = "m³/s"
                elif "m3/s" in flow_unit_raw:
                    flow_unit = "m³/s"

        parsed_data = {
            "height": float(height_str),
            "flow": float(flow_str),
            "station_id": station_id,
            "station_name": station_name,
            "timestamp_from_table_local": datetime_aware_local_quebec.isoformat(),
            "timestamp_from_table_utc": datetime_utc.isoformat(),
            "flow_unit_of_measurement": flow_unit,
            "height_unit_of_measurement": height_unit,
            "flow_friendly_name": f"{station_name} - Débit Actuel",
            "height_friendly_name": f"{station_name} - Niveau Actuel",
            "flow_icon": "mdi:water-sync",
            "height_icon": "mdi:ruler",
            "flow_device_class": "volume_flow_rate",
            "height_device_class": "water",
            "flow_state_class": "measurement",
            "height_state_class": "measurement",
        }
        logger.info(
            "Successfully parsed data: Date=%s, Time=%s, Height=%sm, Flow=%sm³/s",
            date_str,
            time_str,
            float(height_str),
            float(flow_str),
        )
        return parsed_data

    except (IndexError, ValueError, AttributeError) as e:
        logger.error(f"Error parsing data from table row or cell: {e}")
        return None


# --- REST OF THE SCRIPT (send_to_home_assistant and main block) ---
def send_to_home_assistant(
    data, ha_api_base_url, ha_headers, flow_entity_id, height_entity_id, source_url
):
    """Sends the parsed data to Home Assistant via REST API."""
    if not data:
        logger.warning("No data to send to Home Assistant.")
        return

    script_current_local_time = datetime.now(QUEBEC_TZ)

    flow_payload = {
        "state": data["flow"],
        "attributes": {
            "friendly_name": data["flow_friendly_name"],
            "unit_of_measurement": data["flow_unit_of_measurement"],
            "icon": data["flow_icon"],
            "device_class": data["flow_device_class"],
            "state_class": data["flow_state_class"],
            "timestamp": data["timestamp_from_table_local"],
            "last_updated": script_current_local_time.isoformat(),
            "last_changed": data["timestamp_from_table_local"],
            "height_m": data["height"],
            "station_id": data["station_id"],
            "station_name": data["station_name"],
            "source_url": source_url,
        },
    }

    height_payload = {
        "state": data["height"],
        "attributes": {
            "friendly_name": data["height_friendly_name"],
            "unit_of_measurement": data["height_unit_of_measurement"],
            "icon": data["height_icon"],
            "device_class": data["height_device_class"],
            "state_class": data["height_state_class"],
            "timestamp": data["timestamp_from_table_local"],
            "last_updated": script_current_local_time.isoformat(),
            "last_changed": data["timestamp_from_table_local"],
            "flow_m3_s": data["flow"],
            "station_id": data["station_id"],
            "station_name": data["station_name"],
            "source_url": source_url,
        },
    }

    flow_api_url = f"{ha_api_base_url}/states/{flow_entity_id}"
    height_api_url = f"{ha_api_base_url}/states/{height_entity_id}"

    logger.debug(f"Sending data to Home Assistant REST API for {flow_entity_id}")
    try:
        response_flow = requests.post(
            flow_api_url, json=flow_payload, headers=ha_headers, timeout=10
        )
        response_flow.raise_for_status()
        logger.info(
            f"River flow data successfully sent to HA. Status: {response_flow.status_code}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending river flow data to Home Assistant: {e}")

    logger.debug(f"Sending data to Home Assistant REST API for {height_entity_id}")
    try:
        response_height = requests.post(
            height_api_url, json=height_payload, headers=ha_headers, timeout=10
        )
        response_height.raise_for_status()
        logger.info(
            f"River height data successfully sent to HA. Status: {response_height.status_code}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending river height data to Home Assistant: {e}")


def run_fetcher(runtime_config, ha_headers):
    """Execute fetch, parse, and send logic once."""
    parsed_data = fetch_and_parse_data(
        runtime_config["data_url"],
        runtime_config["station_number"],
        runtime_config["station_name_prefix"],
        runtime_config["river_name_override"],
        runtime_config["river_name_fallback"],
    )
    if parsed_data:
        send_to_home_assistant(
            parsed_data,
            runtime_config["ha_api_base_url"],
            ha_headers,
            runtime_config["ha_flow_entity_id"],
            runtime_config["ha_height_entity_id"],
            runtime_config["data_url"],
        )
    else:
        logger.warning("Failed to fetch or parse river data. Not sending to Home Assistant.")


if __name__ == "__main__":
    logger.info("Starting river data fetch script with embedded scheduler")

    try:
        import pytz
    except ImportError:
        logger.error(
            "'pytz' library not found. Please install it using: pip3 install pytz"
        )
        exit(1)

    args = parse_args()
    runtime_config = build_runtime_config(args)

    ha_long_lived_token = args.ha_token or load_ha_token()
    ha_headers = {
        "Authorization": f"Bearer {ha_long_lived_token}",
        "Content-Type": "application/json",
    }

    # Get interval from env variable (in minutes, default 10)
    interval_minutes = int(os.environ.get("FETCHER_INTERVAL_MINUTES", "10"))

    # Set up scheduler
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_fetcher,
        "interval",
        minutes=interval_minutes,
        args=[runtime_config, ha_headers],
        id="fetcher_job",
    )

    logger.info(f"Scheduled fetcher to run every {interval_minutes} minutes")
    logger.info("Scheduler starting...")

    # Run initial fetch immediately
    logger.info("Running initial fetch...")
    run_fetcher(runtime_config, ha_headers)

    # Start scheduler (blocking)
    scheduler.start()

import requests
import os
import signal
import logging
import sys
import json
import time
import glob
from datetime import datetime

from flask import Flask, request, jsonify
from waitress import serve

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
log_levels = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
logging.basicConfig(
    level = log_levels.get(LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)
log.debug("logging initialized")

log.debug("reading in initial env vars")
# Read in other env vars
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_URL = os.environ.get("IMMICH_URL", "")
IMMICH_ALBUM_ID = os.environ.get("IMMICH_ALBUM_ID", "")
JSON_PATH = os.environ.get("WHIMMICH_JSON_PATH", "") # no logging if not specified
HOOK_MODE = os.environ.get("WHIMMICH_HOOK_MODE", "other")
JSON_ASSETID_KEY = os.environ.get("WHIMMICH_JSON_ASSETID_KEY", "")
JSON_ACCEPT_VALUE = os.environ.get("WHIMMICH_JSON_ACCEPT_VALUE", "")
JSON_ACCEPT_KEY = os.environ.get("WHIMMICH_JSON_ACCEPT_KEY", "")
LOG_ROTATE_HOURS = int(os.environ.get("WHIMMICH_LOG_ROTATE_HOURS", "168"))
LOG_IP_TO_FILENAME = bool(os.environ.get("WHIMMICH_LOG_IP_TO_FILENAME", False))

JSON_ASSETID_SUBKEY = os.environ.get("WHIMMICH_JSON_ASSETID_SUBKEY", "")
SUBPATH = os.environ.get("WHIMMICH_SUBPATH", "")

FRAME_ACCEPT_KEY = "Name"
FRAME_ACCEPT_VALUE = "ImageRequestedNotification"
FRAME_ASSETID_KEY = "RequestedImageId"

last_cleanup_time = 0  # Global variable to store the last cleanup timestamp
CLEANUP_INTERVAL = 60  # Interval in seconds (e.g., 3600 seconds = 1 hour)

app = Flask(__name__)
log.debug("Flask app initialized")

def log_file_contents(file_partial, data, ip):
    if not JSON_PATH:
        return
    date = datetime.now().strftime('%Y-%m-%d')
    file_path = f"{JSON_PATH}/{date}_{file_partial}"
    if LOG_IP_TO_FILENAME:
        file_path += f"_{ip}"
    file_path += ".txt"
    log.debug(f"attempting to log to {file_path}")
    try:
        with open(file_path, 'a') as file:  # Append mode to not overwrite
            json.dump(data, file)
            file.write("\n")  # Add newline for each entry for easier reading
            log.debug(f"Logged payload to file: {file_path}")
    except Exception as e:
        log.error(f"Error writing to file {file_path}: {e}")

@app.route(f"{SUBPATH}/hook", methods=['POST'])
def hook():
    data = request.json # Get the JSON data from the request

    time_unix = time.time()
    time_pretty = datetime.fromtimestamp(time_unix).strftime("%Y-%m-%d %H:%M:%S")
    ip = request.remote_addr  # Get the IP address of the client
    assets = []

    # Print the received payload to stdout
    log.debug(f"Received webhook data from IP {ip}: {data}")
    add_fields = { "received_time": time_pretty, "received_time_unix": time_unix, "ip_source": ip }
    send_log = { "received_json": data }
    send_log |= add_fields

    log_file_contents("incoming", send_log, ip )

    # Check if the JSON payload contains "Name": "ImageRequestedNotification"
    if JSON_ACCEPT_KEY and JSON_ACCEPT_VALUE: # only look for certain events
        if data.get(JSON_ACCEPT_KEY) == JSON_ACCEPT_VALUE:
            log.debug(f"payload '{JSON_ACCEPT_KEY}' is '{JSON_ACCEPT_VALUE}', continuing")
        else:
            log.warning(f"Webhook payload ignored. '{JSON_ACCEPT_KEY}' is not '{JSON_ACCEPT_VALUE}''.")
            return jsonify({"status": "ignored", "message": f"'{JSON_ACCEPT_KEY}' is not '{JSON_ACCEPT_VALUE}'"}), 200
    else:
        log.debug("JSON_ACCEPT_KEY and/or JSON_ACCEPT_VALUE not set, subscribing to all events")

    match HOOK_MODE:
        case 'immich-frame':
            log.debug(f"using immich-frame compatability mode (single layer JSON) with key {JSON_ASSETID_KEY}")
            assets.append(data.get(JSON_ASSETID_KEY))
        case 'immich-kiosk':
            log.debug("kiosk compatibility mode (JSON with subarray) with key {JSON_ASSETID_KEY} and subkey {JSON_ASSETID_SUBKEY}")
            for id_slice in data.get(JSON_ASSETID_KEY):
                assets.append(id_slice.get(JSON_ASSET_ID_SUBKEY))
        case _:
            log.debug("no compatibility mode set, using default")

    # Ensure payload contains assetId
    if not assets:
        log.error(f"previous payload did not contain '{JSON_ASSETID_KEY}")
        return jsonify({"status": "error", "message": f"Missing '{JSON_ASSETID_KEY}' in payload"}), 400

    log.debug(f"Identified asset {assets}. Continuing with processing.")

    global last_assets
    last_assets = { "assets": assets }
    last_assets |= add_fields

    set_favorite(assets)
    return add_to_album(assets)

@app.route(f"{SUBPATH}/last", methods=['GET'])
def last():
    log.info(f"last asset requested, returning {last_assets}")
    return jsonify(last_assets), 200

def call_immich(payload, suburl):
    if not IMMICH_API_KEY or not IMMICH_URL:
        return
    try:
        headers = {
            "x-api-key": IMMICH_API_KEY,
            "Content-Type": "application/json"
        }
        log.debug(f"headers: {headers}, payload: {payload}")
        response = requests.put(f"{IMMICH_URL}/api{suburl}", json=payload, headers=headers)
        if response.status_code == 200:
            log.debug(f"Successfully called {suburl} with {payload}.")
            return jsonify({"status": "success", "message": "Asset processed successfully"}), 200
        else:
            log.error(f"Failed to process asset. Status code: {response.status_code}, Response: {response.text}")
            return jsonify({"status": "error", "message": "Failed to process asset"}), response.status_code

    except requests.exceptions.RequestException as e:
        log.error(f"Error interacting with Immich API: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

def add_to_album(asset_ids):
    log.info(f"adding asset {asset_ids} to album {IMMICH_ALBUM_ID}")
    payload = {"ids": asset_ids}
    return call_immich(payload, f'/albums/{IMMICH_ALBUM_ID}/assets')

def set_favorite(asset_ids):
    log.info(f"setting assets {asset_ids} as favorites")
    payload = {"ids": asset_ids, "isFavorite": True}
    log.debug(payload)
#    return call_immich(payload, '/assets')

# Health check route
@app.route(f"{SUBPATH}/health", methods=['GET'])
def health_check():
    log.debug("responding to healthcheck endpoint")
    health_reply = { "status": "healthy" }
    current_time = time.time()  # Current timestamp in seconds
    if JSON_PATH:
        if current_time - last_cleanup_time >= CLEANUP_INTERVAL*60:
            log.info(f"{CLEANUP_INTERVAL} minutes has passed since the last cleanup. Running log cleanup.")
            try:
                cleanup_logs(JSON_PATH)  # Run the log cleanup
            except Exception as e:
                log.error(f"Error during log cleanup: {e}")
        health_reply["last_cleanup_unix"] = last_cleanup_time
        health_reply["last_cleanup"] = datetime.fromtimestamp(last_cleanup_time).strftime("%Y-%m-%d %H:%M:%S")

    return jsonify(health_reply), 200

def handle_shutdown_signal(signum, frame):
    log.info("Shutdown signal received. Gracefully shutting down...")
    sys.exit(0)

def check_env():
    # Check for required or recommended env vars
    log.debug("Beginning environment variable checks")
    if not IMMICH_API_KEY or not IMMICH_URL:
        log.error("IMMICH_API_KEY or IMMICH_URL is not provided. Logging will be to local files only")

    if not IMMICH_ALBUM_ID:
        log.warning("no IMMICH_ALBUM_ID provided")

    if not JSON_PATH:
        log.warning("JSON_PATH not set, logging disabled")

    if SUBPATH and (not SUBPATH.startswith('/') or SUBPATH.endswith('/')):
        log.fatal(f"invalid subpath specified. SUPATH={SUBPATH}. exiting")
        sys.exit(1)

    match HOOK_MODE:
        case "immich-frame":
            log.info("using immich-frame compatibility mode")
            global JSON_ASSETID_KEY , JSON_ACCEPT_VALUE , JSON_ACCEPT_KEY
            JSON_ASSETID_KEY = FRAME_ASSETID_KEY
            JSON_ACCEPT_VALUE = FRAME_ACCEPT_VALUE
            JSON_ACCEPT_KEY = FRAME_ACCEPT_KEY
            log.debug(f"JSON_ACCEPT_KEY={JSON_ACCEPT_KEY} - JSON_ACCEPT_VALUE={JSON_ACCEPT_VALUE} - JSON_ASSETID_KEY='{JSON_ASSETID_KEY}")
        case "immich-kiosk":
            log.info("using immich-kiosk compatiblity mode")
        case "other":
            log.warning("Using 'other' compatbility mode, results are untested")
        case _:
            log.fatal(f"WHIMMICH_HOOK_MODE={HOOK_MODE} unknown, exiting. Please set this variable to a supported value")
            sys.exit(1)
    log.debug("Completed environment variable checks")

def cleanup_logs(log_dir, max_age_seconds=3600 * LOG_ROTATE_HOURS):
    if not log_dir:
        log.debug("cleanup logs called, but no path specified. skipping")
        return
    now = time.time()
    log.debug(f"beginning log cleanup, cleaning up all files over {LOG_ROTATE_HOURS} hours mod time")
    for file_path in glob.glob(f"{log_dir}/*.txt"):
        if os.path.getmtime(file_path) < (now - max_age_seconds):
            try:
                os.remove(file_path)
                log.info(f"Deleted old log file: {file_path}")
            except Exception as e:
                log.error(f"Error deleting file {file_path}: {e}")
    global last_cleanup_time
    last_cleanup_time = now  # Update the last cleanup time


if __name__ == '__main__':
    log.info("whImmich starting up")

    # Set up signal handling for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown_signal)  # For Ctrl+C (SIGINT)
    signal.signal(signal.SIGTERM, handle_shutdown_signal) # For docker stop (SIGTERM)

    check_env()

    if JSON_PATH:
        cleanup_logs(JSON_PATH)
    port = int(os.environ.get("WHIMMICH_PORT", 5000))
    host = os.environ.get("WHIMMICH_HOST", "0.0.0.0")
    log.debug(f"collected startup info, host {host}, port {port}, subhook path='{SUBPATH}'")

    # Start serving the Flask app with Waitress
    serve(app, host=host, port=port)

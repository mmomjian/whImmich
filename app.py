import requests
import os
import signal
import logging
import sys
import json

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
HOOK_MODE = os.environ.get("WHIMMICH_HOOK_MODE", "")
JSON_ASSETID_KEY = os.environ.get("WHIMMICH_JSON_ASSETID_KEY", "")
JSON_ACCEPT_VALUE = os.environ.get("WHIMMICH_JSON_ACCEPT_VALUE", "")
JSON_ACCEPT_KEY = os.environ.get("WHIMMICH_JSON_ACCEPT_KEY", "")

JSON_ASSETID_SUBKEY = os.environ.get("WHIMMICH_JSON_ASSETID_SUBKEY", "")
SUBPATH = os.environ.get("WHIMMICH_SUBPATH", "")

FRAME_ACCEPT_KEY = "Name"
FRAME_ACCEPT_VALUE = "ImageRequestedNotification"
FRAME_ASSETID_KEY = "RequestedImageId"

app = Flask(__name__)
log.debug("Flask app initialized")

log.debug("declaring functions")

def log_file_contents(file_dir, data):
    file_path = f"{file_dir}/out.txt"
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

    # Print the received payload to stdout
    log.debug(f"Received webhook data: {data}")
    if JSON_PATH:
        log.debug(f"writing JSON to log file in folder {JSON_PATH}")
        log_file_contents(JSON_PATH, data)

    # Check if the JSON payload contains "Name": "ImageRequestedNotification"
    if JSON_ACCEPT_KEY and JSON_ACCEPT_VALUE: # only look for certain events
        if data.get(JSON_ACCEPT_KEY) == JSON_ACCEPT_VALUE:
            log.debug(f"payload '{JSON_ACCEPT_KEY}' is '{JSON_ACCEPT_VALUE}', continuing")
        else:
            log.warning(f"Webhook payload ignored. '{JSON_ACCEPT_KEY}' is not '{JSON_ACCEPT_VALUE}''.")
            return jsonify({"status": "ignored", "message": f"'{JSON_ACCEPT_KEY}' is not '{JSON_ACCEPT_VALUE}'"}), 200
    else:
        log.debug("JSON_ACCEPT_KEY and/or JSON_ACCEPT_VALUE not set, subscribing to all events")

    global last_asset , last_time
    last_asset = []
    if HOOK_MODE == 'immich-frame':
        log.debug(f"using immich-frame compatability mode (single layer JSON) with key {JSON_ASSETID_KEY}")
        last_asset.append(data.get(JSON_ASSETID_KEY))
    elif HOOK_MODE == 'immich-kiosk':
        log.debug("kiosk compatibility mode (JSON with subarray) with key {JSON_ASSETID_KEY} and subkey {JSON_ASSETID_SUBKEY}")
        for id_slice in data.get(JSON_ASSETID_KEY):
            last_asset.append(id_slice.get(JSON_ASSET_ID_SUBKEY))
    else:
        log.debug("no compatibility mode set, using default")

    # Ensure payload contains assetId
    if not last_asset:
        log.error(f"previous payload did not contain '{JSON_ASSETID_KEY}")
        return jsonify({"status": "error", "message": f"Missing '{JSON_ASSETID_KEY}' in payload"}), 400

    log.debug(f"Identified asset {last_asset}. Continuing with processing.")

    return add_to_album(last_asset)

@app.route(f"{SUBPATH}/last", methods=['GET'])
def last():
    log.info(f"last asset requested, returning {asset}")
    return jsonify({ "ids": last_asset, "timestamp": last_time }), 200

def call_immich(payload, suburl):
    try:
        headers = {
            "x-api-key": f"{IMMICH_API_KEY}",
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
    payload = {"ids": asset_ids, "isFavorite": true}
    return call_immich(payload, '/assets')

# Health check route
@app.route(f"{SUBPATH}/health", methods=['GET'])
def health_check():
    log.debug("responding to healthcheck endpoint")
    return jsonify({"status": "healthy"}), 200

def handle_shutdown_signal(signum, frame):
    log.info("Shutdown signal received. Gracefully shutting down...")
    sys.exit(0)

def check_env():
    # Check for required or recommended env vars
    log.debug("Beginning environment variable checks")
    if not IMMICH_API_KEY or not IMMICH_URL:
        log.fatal("IMMICH_API_KEY and IMMICH_URL must be set as environment variables.")
        sys.exit(1)

    if not IMMICH_ALBUM_ID:
        log.warning("no IMMICH_ALBUM_ID provided")

    if not JSON_PATH:
        log.warning("JSON_PATH not set, logging disabled")

    if SUBPATH and (not SUBPATH.startswith('/') or SUBPATH.endswith('/')):
        log.fatal(f"invalid subpath specified. SUPATH={SUBPATH}. exiting")

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

if __name__ == '__main__':
    log.info("whImmich starting up")

    # Set up signal handling for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown_signal)  # For Ctrl+C (SIGINT)
    signal.signal(signal.SIGTERM, handle_shutdown_signal) # For docker stop (SIGTERM)

    check_env()

    print(f"{JSON_ACCEPT_VALUE} - {JSON_ACCEPT_KEY}")

    port = int(os.environ.get("WHIMMICH_PORT", 5000))
    host = os.environ.get("WHIMMICH_HOST", "0.0.0.0")
    log.debug(f"collected startup info, host {host}, port {port}, subhook path='{SUBPATH}'")

    # Start serving the Flask app with Waitress
    serve(app, host=host, port=port)

import requests
import os
import signal
import logging
import sys
import json
import time
import glob

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

SUBPATH = os.environ.get("WHIMMICH_SUBPATH", "")
JSON_PATH = os.environ.get("WHIMMICH_JSON_PATH", "") # no logging if not specified
JSON_CLIENT_KEY = os.environ.get("WHIMMICH_JSON_CLIENT_KEY", "")
HOOK_MODE = os.environ.get("WHIMMICH_HOOK_MODE", "other")
LOG_ROTATE_HOURS = int(os.environ.get("WHIMMICH_LOG_ROTATE_HOURS", 168))

bool_accept = [ "true", "1", "yes", 1, True ]
LOG_IP_TO_FILENAME = os.environ.get("WHIMMICH_LOG_IP_TO_FILENAME", "false").lower() in bool_accept

JSON_ACCEPT_VALUE = os.environ.get("WHIMMICH_JSON_ACCEPT_VALUE", "")
JSON_ACCEPT_KEY = os.environ.get("WHIMMICH_JSON_ACCEPT_KEY", "")
JSON_ASSETID_KEY = os.environ.get("WHIMMICH_JSON_ASSETID_KEY", "")
JSON_ASSETID_SUBKEY = os.environ.get("WHIMMICH_JSON_ASSETID_SUBKEY", "")
JSON_NEWASSET_VALUE = os.environ.get("WHIMMICH_JSON_NEWASSET_VALUE", "")
JSON_PREFETCH_VALUE = os.environ.get("WHIMMICH_JSON_PREFETCH_VALUE", "")
API_KEY = os.environ.get("WHIMMICH_API_KEY", "")

KEEP_ASSET_LIST = int(os.environ.get("WHIMMICH_KEEP_ASSET_LIST", 10))

DOUBLE_DELAY = float(os.environ.get("WHIMMICH_DOUBLE_DELAY", 0.3))
DISABLE_DOUBLE = os.environ.get("WHIMMICH_DISABLE_DOUBLE", "false").lower() in bool_accept

last_cleanup_time = 0  # Global variable to store the last cleanup timestamp
CLEANUP_INTERVAL = 60  # Interval in minutes
all_assets = {}
next_asset = {}
DEFAULT_CLIENT = 'unknown'
# DISABLE_CLIENT_TRACKING = os.environ.get("WHIMMICH_DISABLE_CLIENT_TRACKING", "false").lower() in bool_accept

JSON_UNAUTH = { "status": "unauthorized" }
JSON_SUCCESS = { "status": "success" }
JSON_ERROR = { "status": "error" }

app = Flask(__name__)
log.debug("Flask app initialized")

@app.before_request
def check_api_key():
    if request.endpoint in ['health_check']: # skip auth for healthcheck
        return
    if not API_KEY:
      return
    apikey = request.headers.get('X-API-Key', None)
    if apikey == API_KEY:
      return
    if request.method == 'POST':
      apikey = request.json.get('apikey', None)
      if apikey == API_KEY:
        return
    apikey = request.args.get('apikey', None)
    if apikey == API_KEY:
      return
    return jsonify({"status": JSON_UNAUTH}), 401

def log_file_contents(file_partial, data, ip):
    if not JSON_PATH:
        return
    date = time.strftime('%Y-%m-%d', time.localtime(time.time()))
    file_path = f"{JSON_PATH}/{date}_{file_partial}"
    if LOG_IP_TO_FILENAME:
        file_path += f"_{ip}"
    file_path += ".log"
    log.debug(f"attempting to log to {file_path}")
    try:
        with open(file_path, 'a') as file:  # Append mode to not overwrite
            json.dump(data, file)
            file.write("\n")
            log.debug(f"Logged payload to file: {file_path}")
    except Exception as e:
        log.error(f"Error writing to file {file_path}: {e}")

def hook_accept_key_value(data_hook, key, value_arg):
    if not isinstance(value_arg, list):
      value = [ value_arg ]
    else:
      value = value_arg
    if data_hook.get(key) in value:
        log.debug(f"{key} matches {value}, in hook_accept_key_value, returning True")
        return True
    log.debug("returning false from hook_accept_key_value")
    return False

def return_client(request):
    client = req_client(request)
    if client:
      return client
    return DEFAULT_CLIENT

def init_client(name):
  if not all_assets.get(name, None):
    all_assets[name] = []
  if not next_asset.get(name, None):
    next_asset[name] = []
  return

def req_client(request):
    if request.method == 'POST' and JSON_CLIENT_KEY:
      client = request.json.get(JSON_CLIENT_KEY, None)
      if client:
        init_client(client)
        return client
    client = request.args.get('client', None)
    if client:
      init_client(client)
      return client
    return None

@app.route(f"{SUBPATH}/prefetch", methods=['POST', 'GET'])
def prefetch():
#    data = request.json # Get the JSON data from the request
#    log.debug(request.json)
    client = return_client(request)
#    if not client:
#      return jsonify({ "client_prefetch": next_asset } | JSON_SUCCESS), 200
    return jsonify({ "next_asset": next_asset.get(client, [])} | JSON_SUCCESS), 200

@app.route(f"{SUBPATH}/hook", methods=['POST'])
def hook():
    data = request.json # Get the JSON data from the request
    client = return_client(request)

    time_unix = time.time()
    time_pretty = pretty_time(time_unix)
    ip = request.remote_addr  # Get the IP address of the client
    assets = []

    # Print the received payload to stdout
    log.debug(f"Received webhook data from IP {ip}: {data}")
    add_fields = { "time_received": time_pretty, "time_received_unix": time_unix, "client_ip": ip, "client_name": client,
      "hook_json": [ data ], "multi_delay": None, "time_ended": None, "time_ended_unix": None }

    send_log = { "hook_json": data }
    send_log |= add_fields

    log_file_contents("incoming", send_log, ip )

    # Check if the JSON payload contains "Name": "ImageRequestedNotification"
    if JSON_ACCEPT_KEY and JSON_ACCEPT_VALUE: # only look for certain events
        if not hook_accept_key_value(data, JSON_ACCEPT_KEY, JSON_ACCEPT_VALUE):
            log.warning(f"Webhook payload ignored. '{JSON_ACCEPT_KEY}' is not '{JSON_ACCEPT_VALUE}''.")
            return jsonify({"status": "ignored", "message": f"'{JSON_ACCEPT_KEY}' is not '{JSON_ACCEPT_VALUE}'"}), 200
    else:
        log.debug("JSON_ACCEPT_KEY and/or JSON_ACCEPT_VALUE not set, subscribing to all events")

    event_type = data.get(JSON_ACCEPT_KEY)
    log.debug(f"event type: {event_type}")
    match HOOK_MODE:
        case 'immich-frame':
            log.debug(f"using immich-frame compatability mode (single layer JSON) with key {JSON_ASSETID_KEY}")
            assets.append(data.get(JSON_ASSETID_KEY))
        case 'immich-kiosk':
          log.debug("kiosk compatibility mode (JSON with subarray) with key {JSON_ASSETID_KEY} and subkey {JSON_ASSETID_SUBKEY}")
          log.debug(f"{event_type} --- {JSON_PREFETCH_VALUE}")
          for x_asset in data.get(JSON_ASSETID_KEY):
              log.debug(f"found asset: {x_asset}")
              assets.append(x_asset['id'])
          if event_type == JSON_PREFETCH_VALUE:
            next_asset[client] = assets
            next_asset[client].append(add_fields)
            log.debug("storing prefetch asset")
            return jsonify({ "message": "stored next assets"} | JSON_SUCCESS), 200
        case _:
            log.debug("no compatibility mode set, using default")

    log.debug(f"full asset array: {assets}")
    # Ensure payload contains assetId
    if not assets or assets == None:
        log.error(f"previous payload did not contain '{JSON_ASSETID_KEY}'")
        return jsonify({"status": "error", "message": f"Missing '{JSON_ASSETID_KEY}' in payload"}), 400

    log.debug(f"Identified asset {assets}. Continuing with processing.")

    rotate_assets(assets, add_fields, client)

    set_favorite(assets)
    return add_to_album(assets)

def rotate_assets(ids, add, client):
    global all_assets
    current_assets = { "assets": ids }
    current_assets |= add
    now = time.time()

    if DOUBLE_DELAY and len(all_assets.get(client, [])) > 0 and not DISABLE_DOUBLE:
#      if not all_assets[client][-1]["client_ip"] == current_assets["client_ip"]:
#        log.debug("second image appears to be from a different IP, skipping")
      time_diff = now - all_assets[client][-1]['time_received_unix']
      log.debug(f"time difference: {time_diff} seconds")
      if time_diff < DOUBLE_DELAY:
          log.debug("time difference identified, processing as duplicate")
          all_assets[client][-1]['assets'].extend(current_assets['assets'])
          all_assets[client][-1]['hook_json'].extend(current_assets['hook_json']) # extend will add to existing list
          all_assets[client][-1]['multi_delay'] = time_diff
          return

#    if client not in all_assets:
#      all_assets[client] = []

    if len(all_assets[client]) > 0:
        end_time = now - 0.1
        all_assets[client][-1]['time_ended'] = pretty_time(end_time)
        all_assets[client][-1]['time_ended_unix'] = end_time

#    all_assets.setdefault("client", []).append([])
    all_assets[client].append(current_assets)
#    all_assets.append(current_assets)
    del all_assets[client][:-KEEP_ASSET_LIST]
    return

def get_asset(list, pos, client):
    if not isinstance(pos, int) or not pos < 0:
        raise ValueError("Position must be a string ('newest', 'second_newest') or an integer.")
    try:
      log.debug(f"{len(list[client])}")
      if len(list[client]) < abs(pos):
          raise IndexError(f"Position {pos} does not exist in the list of size {len(list)}.")
      return list[client][pos]
    except KeyError:
      raise KeyError
      return {"message": f"unknown client '{client}'", "status": JSON_ERROR}

def get_file(n, client):
    try:
        log.debug(f"{n} asset requested, searching")
        reply_json = get_asset(all_assets, n, client)
        reply_json |= { "status": JSON_SUCCESS }
        return jsonify(reply_json), 200
    except (KeyError, IndexError, ValueError) as e:
        log.error(f"unable to return last asset. Possibly no images received yet, or unknown client '{client}' Error {e}")
        return jsonify({ "status": "failure", "failure_message": "No last asset found. Possible first startup?"}), 500

@app.route(f"{SUBPATH}/history", methods=['POST', 'GET'])
def history():
    if request.method == 'GET':
        return jsonify({ "client_assets": all_assets } | JSON_SUCCESS), 200

    # must be a POST
    client = req_client(request)
    data = request.json
    if 'offset' in data and client:
        file_number = data.get('offset')
        return get_file(file_number, client)
    return jsonify({ "client_assets": all_assets } | JSON_SUCCESS), 200

@app.route(f"{SUBPATH}/last", methods=['GET'])
def last():
    client = return_client(request)
    return get_file(-2, client)

@app.route(f"{SUBPATH}/current", methods=['GET'])
def current():
    client = return_client(request)
    return get_file(-1, client)

def immich_headers(apikey):
    if not immich_enabled():
        return None
    return { "x-api-key": apikey, "Content-Type": "application/json" }

def immich_enabled():
    if IMMICH_API_KEY and IMMICH_URL:
        return True
    return False

def call_immich(payload, suburl):
    if not immich_enabled():
        return None
    try:
        headers = immich_headers(IMMICH_API_KEY)

        log.debug(f"headers: {headers}, payload: {payload}")
        response = requests.put(f"{IMMICH_URL}/api{suburl}", json=payload, headers=headers)
        if response.status_code == 200:
            log.debug(f"Successfully called {suburl} with {payload}.")
            return jsonify({"status": "success", "message": "Asset processed successfully"}), 200
        else:
            log.error(f"Failed to process asset. Status code: {response.status_code}, Response: {response.text}")
            return jsonify({"message": "Failed to process asset"} | JSON_ERROR), response.status_code

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

def logs_enabled():
    if JSON_PATH:
        log.debug("returning True from logs_enabled")
        return True
    log.debug("returning False from logs_enabled")
    return False

def log_cleanup_due(last_arg):
    current_time = time.time()
    if logs_enabled() and current_time - last_arg >= CLEANUP_INTERVAL*60:
        log.info(f"{CLEANUP_INTERVAL} minutes has passed since the last cleanup.")
        return True
    log.debug("returning False from log_cleanup_due")
    return False

# Health check route
@app.route(f"{SUBPATH}/health", methods=['GET'])
def health_check():
    log.debug("responding to healthcheck endpoint")
    health_reply = { "status": "healthy" }

    cleanup_logs(JSON_PATH)  # Run the log cleanup
    if logs_enabled():
        health_reply["last_cleanup_unix"] = last_cleanup_time
        health_reply["last_cleanup"] = pretty_time(last_cleanup_time)

    return jsonify(health_reply), 200

def handle_shutdown_signal(signum, frame):
    log.info("Shutdown signal received. Gracefully shutting down...")
    sys.exit(0)

def pretty_time(timestamp):
    if timestamp <= 0:  # Handle invalid or unset timestamps
        return "Never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) # currently prints the pretty time in local time with no TZ included

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

    global JSON_ASSETID_KEY , JSON_ACCEPT_VALUE , JSON_ACCEPT_KEY , JSON_NEWASSET_VALUE , JSON_PREFETCH_VALUE , DOUBLE_DELAY , JSON_CLIENT_KEY
    match HOOK_MODE:
        case "immich-frame":
            log.info("using immich-frame compatibility mode")
            JSON_ASSETID_KEY = "RequestedImageId"
            JSON_ACCEPT_KEY = "Name"
            JSON_ACCEPT_VALUE = [ "ImageRequestedNotification" ]
            JSON_CLIENT_KEY = "ClientIdentifier"
            DOUBLE_DELAY = 0.3
            log.debug(f"JSON_ACCEPT_KEY={JSON_ACCEPT_KEY} - JSON_ACCEPT_VALUE={JSON_ACCEPT_VALUE} - JSON_ASSETID_KEY={JSON_ASSETID_KEY}")
        case "immich-kiosk":
            log.info("using immich-kiosk compatiblity mode")
            JSON_ACCEPT_KEY = "event"
            JSON_NEWASSET_VALUE = "asset.new"
            JSON_PREFETCH_VALUE = 'asset.prefetch'
            JSON_ACCEPT_VALUE = [ JSON_NEWASSET_VALUE , JSON_PREFETCH_VALUE ]
            JSON_CLIENT_KEY = "clientName"
            JSON_ASSETID_KEY = "assets"
        case "other":
            log.warning("Using 'other' compatbility mode, results are untested")
        case _:
            log.fatal(f"WHIMMICH_HOOK_MODE={HOOK_MODE} unknown, exiting. Please set this variable to a supported value")
            sys.exit(1)
    log.debug("Completed environment variable checks")

def cleanup_logs(log_dir, max_age_hours = LOG_ROTATE_HOURS):
    global last_cleanup_time
    max_age_seconds = 3600 * max_age_hours
    if not log_dir or not logs_enabled():
        log.debug("cleanup logs called, but no path specified or disabled. skipping")
        return
    if not log_cleanup_due(last_cleanup_time):
        log.debug(" in cleanup_logs: cleanup not due")
        return
    now = time.time()
    log.debug(f"beginning log cleanup, cleaning up all files over {LOG_ROTATE_HOURS} hours mod time")
    for file_path in glob.glob(f"{log_dir}/*.log"):
        if os.path.getmtime(file_path) < (now - max_age_seconds):
            try:
                os.remove(file_path)
                log.info(f"Deleted old log file: {file_path}")
            except Exception as e:
                log.error(f"Error deleting file {file_path}: {e}")
    last_cleanup_time = now  # Update the last cleanup time


if __name__ == '__main__':
    log.info("whImmich starting up")

    # Set up signal handling for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown_signal)  # For Ctrl+C (SIGINT)
    signal.signal(signal.SIGTERM, handle_shutdown_signal) # For docker stop (SIGTERM)

    check_env()

    cleanup_logs(JSON_PATH)
    port = int(os.environ.get("WHIMMICH_PORT", 5000))
    host = os.environ.get("WHIMMICH_HOST", "0.0.0.0")
    log.debug(f"collected startup info, host {host}, port {port}, subhook path='{SUBPATH}'")

    init_client(DEFAULT_CLIENT)
    # Start serving the Flask app with Waitress
    serve(app, host=host, port=port)

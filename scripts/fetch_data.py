#Imports for json, api requests, .env load, etc
import os
import json
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# Load config from .env
load_dotenv()

API_KEY = os.getenv("MTA_API_KEY") # Load API Key
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60")) # Grab custom interval, default =  60
MAX_RAW_SNAPSHOTS = int(os.getenv("MAX_RAW_SNAPSHOTS", "180")) # Load max number of raw snapshots kept

# Find the main project folder by going one folder above /scripts
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Folder where raw MTA snapshots are stored
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")

# Create data/raw if it doesn't already exist
os.makedirs(RAW_DIR, exist_ok=True)

# Full file path where all raw snapshots are saved
RAW_OUTPUT = os.path.join(
    RAW_DIR,
    "all_buses_snapshots.jsonl"
)

VEHICLE_MONITORING_URL = "https://bustime.mta.info/api/siri/vehicle-monitoring.json" # API Endpoint  (bus id, route, direction, location, etc)


# Current UTC time in ISO format
def now_iso():
    return datetime.now(timezone.utc).isoformat()


# Load all existing JSONL lines from the raw snapshot file
# Each line represents one full snapshot
def load_jsonl_lines(path):
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


# rewrite the raw snapshot file using a temporary file first
# This helps avoid leaving behind a half-written file if something goes wrong
def write_jsonl_lines(path, lines):
    temp_path = path + ".tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

    os.replace(temp_path, path)


# Append a new snapshot and keep only the most recent max_rows entries
# This turns the raw file into a rolling window instead of letting it grow forever
def append_jsonl_rolling(path, row, max_rows):
    lines = load_jsonl_lines(path)
    lines.append(json.dumps(row))

    if max_rows > 0:
        lines = lines[-max_rows:]

    write_jsonl_lines(path, lines)

# Normalize text values from the API
def first_text(value): 
    if isinstance(value, list): # If API value is a list convert it into a string
        if not value:
            return "" # Error Handling: return an empty string if value (list) = empty
        return str(value[0]).strip() # return converted string
    if value is None: # Error Handling: return an empty string if value is empty
        return ""
    return str(value).strip() # return a string regardless of whether it's a list or not 


# Pull the whole vehicle feed
def fetch_vehicle_monitoring():
    if not API_KEY:
        raise ValueError("Missing MTA_API_KEY in .env") # Error Handling: API Key is needed

    params = {
        "key": API_KEY,
        "version": 2,  # 2 is preferable (referenced from mta documentation)
        "VehicleMonitoringDetailLevel": "normal", # Default
    }

    response = requests.get(VEHICLE_MONITORING_URL, params=params, timeout=30) # makes a request and waits 30 seconds for a response
    response.raise_for_status() # Error Handling: return a status code if an error occurs
    return response.json() # converts the API response from Json text into a python dictionary


# Turn the raw API response into cleaner bus rows
def extract_bus_rows(payload):
    rows = [] # Empty list that'll hold one cleaned dictionary per bus

    try: # The MTA response is deeply nested
        delivery = payload["Siri"]["ServiceDelivery"]["VehicleMonitoringDelivery"][0] # fetch the dictionary that contains live bus entries
        activities = delivery.get("VehicleActivity", []) # list of buses
    except Exception:
        return rows # If the structure is missing/malformed return an empty list

    for activity in activities: # loop through each bus activity
        try:
            monitored_journey = activity.get("MonitoredVehicleJourney", {}) # Main route & bus info
            monitored_call = monitored_journey.get("MonitoredCall", {}) # Stop-related info (next stop & ETA)
            location = monitored_journey.get("VehicleLocation", {}) # Lat, Long coordinates

            row = {
                "vehicle_ref": first_text(monitored_journey.get("VehicleRef")), # bus ID
                "line_ref": first_text(monitored_journey.get("LineRef")), # internal route reference
                "direction_ref": first_text(monitored_journey.get("DirectionRef")), # returns 0 or 1 (i.e direction of the bus)
                "destination_name": first_text(monitored_journey.get("DestinationName")), # the destination name
                "published_line_name": first_text(monitored_journey.get("PublishedLineName")), # route name (ex: M15 or M15-SBS)
                "recorded_at": first_text(activity.get("RecordedAtTime")), # Timestamp from the API for that bus entry
                "progress_rate": first_text(monitored_journey.get("ProgressRate")), # Whether the bus is moving or not
                "progress_status": first_text(monitored_journey.get("ProgressStatus")), # Layover, in-service, etc
                "bearing": monitored_journey.get("Bearing"), # Direction angle of travel
                "lat": location.get("Latitude"), # coordinate
                "lon": location.get("Longitude"), # coordinate
                "next_stop_name": first_text(monitored_call.get("StopPointName")), # upcoming stop name
                "arrival_proximity_text": first_text(monitored_call.get("ArrivalProximityText")), # Readable arrival text
                "expected_arrival_time": first_text(monitored_call.get("ExpectedArrivalTime")), # ETA
            }

            if row["vehicle_ref"] and row["line_ref"] and row["lat"] is not None and row["lon"] is not None:
                rows.append(row) # keep this bus if it has an ID, route, lat/long coordinates
        except Exception:
            continue # Skip incomplete entries

    return rows # Return rows with sufficient data


def collect_live_route_data(): # Print statements to let user know what's going on
    print(f"Collecting all live bus data every {POLL_INTERVAL_SECONDS} seconds")
    print(f"Writing to {RAW_OUTPUT}\n")

    while True: # Live Collector (take a snpashot)
        ts = now_iso()

        try:
            payload = fetch_vehicle_monitoring() # collect raw JSON
            buses = extract_bus_rows(payload) # clean the raw JSON into usable bus rows

            append_jsonl_rolling( # take the current live bus snapshot, add it to the raw data file, and then keep only the most recent MAX_RAW_SNAPSHOTS snapshots
                RAW_OUTPUT,
                {
                    "snapshot_time": ts,
                    "bus_count": len(buses),
                    "buses": buses,
                },
                MAX_RAW_SNAPSHOTS,
            )

            print(f"[{ts}] saved {len(buses)} buses") # Success log
        except Exception as e:
            print(f"[{ts}] error: {e}") # Error handling

        time.sleep(POLL_INTERVAL_SECONDS) # Continue loop after interval in .env


if __name__ == "__main__":
    collect_live_route_data() # Run loop when file is executed
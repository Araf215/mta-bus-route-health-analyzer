# Imports for file handling, regex, json, time, math
import os
import re
import json
import time
import math
from statistics import median
from datetime import datetime

from dotenv import load_dotenv # load .env
from metrics import compute_route_health # fetch the compute_route_health function for scoring

load_dotenv()

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60")) # grab interval, default = 60

# Find the main project folder by going one folder above /scripts
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Full path to the raw snapshot file created by fetch_data.py
RAW_PATH = os.path.join(
    PROJECT_ROOT,
    "data",
    "raw",
    "all_buses_snapshots.jsonl"
)

# Folder where processed dashboard data is stored
PROCESSED_DIR = os.path.join(
    PROJECT_ROOT,
    "data",
    "processed"
)

# Create data/processed if it doesn't already exist
os.makedirs(PROCESSED_DIR, exist_ok=True)

# Full output path used by the frontend
OUTPUT_PATH = os.path.join(
    PROCESSED_DIR,
    "all_routes_live_metrics.json"
)

MAX_HISTORY_POINTS = int(os.getenv("MAX_RAW_SNAPSHOTS", "90")) # Only 90 snapshots can be processed at once (efficiency)
SMOOTHING_WINDOW = 5 # Use the last 5 computed route points to reduce short-term api noise
MIN_MOVEMENT_METERS = 25 # Bus needs to move at least 25 meters between snapshots (recorded every minute)


def load_jsonl(path): # Reads the raw JSON snapshot
    rows = []
    if not os.path.exists(path): # If the raw data doesn't exist return an empty list
        return rows

    with open(path, "r", encoding="utf-8") as f: # open the file for reading
        for line in f: # read the file one line at a time
            line = line.strip() # remove extra whitespace
            if line: # skip blank lines
                rows.append(json.loads(line)) # converts JSON string into a python dictionary
    return rows # return a full list of snapshots


def parse_dt(value): # Converts timestamps into python datetime objects
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) # Convert Z to  ++00.00 so that datetime.fromisoformat() can parse the timestamp
    except Exception: # If parsing fails return none
        return None


def round_or_none(value, digits=2):
    if value is None:
        return None
    return round(value, digits) # Helper function to round values


def median_or_none(values): 
    if not values:
        return None
    return median(values) # Helper function to return the median


def haversine_meters(lat1, lon1, lat2, lon2): # Using the haversine formula I can get the geographic distance between two lat/long coordinates
    r = 6371000 # earth radius
    p1 = math.radians(lat1) # Coordinates and the difference needs to be in radians
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    ) # Haversine formula 
    return 2 * r * math.asin(math.sqrt(a))


def is_layover(bus):
    # Checks the MTA status fields to see if the bus is actually in a layover
    progress_rate = str(bus.get("progress_rate") or "").strip().lower()
    progress_status = str(bus.get("progress_status") or "").strip().lower()

    return progress_rate == "layover" or "layover" in progress_status


def is_in_service(bus):
    # A bus can still be in service even if it is stopped, so only exclude actual layovers
    return not is_layover(bus)

def classify_service(bus): # classifies bus as sbs or local
    route_name = str(
        bus.get("published_line_name") or bus.get("line_ref") or ""
    ).strip().upper() # uses the route name to determine if the bus is SBS

    if "SBS" in route_name:
        return "sbs"
    return "local"


def infer_borough(route_short_name):
    route = route_short_name.upper().strip() # Capitalize and strip prefix

    # Borough prefix, return borough name
    if route.startswith("BX"):  
        return "Bronx"
    if route.startswith("B"):
        return "Brooklyn"
    if route.startswith("M"):
        return "Manhattan"
    if route.startswith("Q"):
        return "Queens"
    if route.startswith("S"):
        return "Staten Island"
    return "Other" # Some routes might not have a borough prefix (ex: X28 bus)

def clean_destination_label(destination):
    if not destination: # If the destination is missing return none
        return None

    text = str(destination).strip() # convert destination to string
    if not text:
        return None

    text = " ".join(text.split()) 
    return " ".join(word.capitalize() for word in text.split()) # capitalize and remove spacing


def make_direction_info(bus): # Direction key and label reading
    destination = clean_destination_label(bus.get("destination_name"))
    direction_ref = str(bus.get("direction_ref") or "").strip()

    if destination: # Use regex to convert destinations into a underscore  format
        slug = destination.lower().replace("/", "_")
        slug = re.sub(r"[^a-z0-9_ ]", "", slug)
        slug = slug.replace(" ", "_")
        slug = re.sub(r"_+", "_", slug).strip("_")

        return { # Direction key ex: "to_south_ferry", Destination label:  "To South Ferry"
            "direction_key": f"to_{slug}",
            "direction_label": f"To {destination}"
        }

    if direction_ref == "0": #  MTA returns 0 or 1. 0 = outbound
        return {
            "direction_key": "outbound",
            "direction_label": "Outbound"
        }

    if direction_ref == "1": # MTA returns 0 or 1. 1 = inbound
        return {
            "direction_key": "inbound",
            "direction_label": "Inbound"
        }

    return { # Return unknown if destination or direction can't be computed
        "direction_key": "unknown",
        "direction_label": "Unknown"
    }


def index_buses(snapshot): # Creates a lookup table of buses by bus ID
    indexed = {}
    for bus in snapshot.get("buses", []):
        bus_ref = bus.get("vehicle_ref")
        if bus_ref:
            indexed[bus_ref] = bus
    return indexed # If a bus have a vehicle_ref store it in the dictionary: indexed


def compute_bus_movements(current_snapshot, previous_snapshot): # Estimate movement between the current and past snapshot
    if not previous_snapshot: # If a previous snapshot doesn't exist then return an empty dictionary
        return {}

    previous_bus_index = index_buses(previous_snapshot) # Looks up buses from the previous snapshot
    movements = {} # Movement stats by vehicle ID 

    for bus in current_snapshot.get("buses", []): # Loop through all detected buses in the current snapshot
        bus_ref = bus.get("vehicle_ref") 
        previous_bus = previous_bus_index.get(bus_ref) # If a bus from the previous snapshot and current snapshot both exist

        if not bus_ref or not previous_bus:
            continue # Skip buses that have no bus ID or were not in the last snapshot

        try: # Coordinates of the previous and current bus position
            lat1 = float(previous_bus.get("lat"))
            lon1 = float(previous_bus.get("lon"))
            lat2 = float(bus.get("lat"))
            lon2 = float(bus.get("lon"))
        except Exception: # If there's invlid coordinates then skip
            continue

        moved_meters = haversine_meters(lat1, lon1, lat2, lon2) # Perform haversine formula to find the geographic distance the bus moved in the past and current snapshot (polling interval time (1 min))

        current_time = parse_dt(bus.get("recorded_at")) or parse_dt(current_snapshot.get("snapshot_time")) # Current timestamp 
        previous_time = parse_dt(previous_bus.get("recorded_at")) or parse_dt(previous_snapshot.get("snapshot_time")) # Past timestamp

        seconds_elapsed = None # Time-based movement variable
        meters_per_minute = None # Time-based movement variable

        if current_time and previous_time: # If both timestamps exist
            seconds_elapsed = (current_time - previous_time).total_seconds() # Compute elapsed time in seconds
            if seconds_elapsed and seconds_elapsed > 0:
                meters_per_minute = moved_meters / (seconds_elapsed / 60.0) # Compute speed in meters per minute

        movements[bus_ref] = { # Store movement result for this bus
            "moved_meters": round(moved_meters, 2), # Distance moved
            "seconds_elapsed": round(seconds_elapsed, 2) if seconds_elapsed is not None else None, # Time between positions
            "meters_per_minute": round(meters_per_minute, 2) if meters_per_minute is not None else None,  # estimated movement rate
            "is_moving_by_position": (
                meters_per_minute is not None
                and meters_per_minute >= MIN_MOVEMENT_METERS
            ), # Whether it moved at least 25 meters
        }

    return movements


def build_route_groups(snapshot): # Groups buses based on route-direction
    grouped = {} # Stores lists of buses by route key
    direction_labels = {} # Stores the direction label for each route  key

    for bus in snapshot.get("buses", []):
        route_short_name = str(
            bus.get("published_line_name") or bus.get("line_ref") or ""
        ).strip().upper() # Get the route name (Ex: M15)

        if not route_short_name: # Skip if route name not found
            continue

        # Compute borough, direction key, direction label
        borough = infer_borough(route_short_name)
        direction_info = make_direction_info(bus)
        direction_key = direction_info["direction_key"]
        direction_label = direction_info["direction_label"]

        route_key = f"{borough}|{route_short_name}|{direction_key}" # Build the full route key
        grouped.setdefault(route_key, []).append(bus)
        direction_labels[route_key] = direction_label

    return grouped, direction_labels # Return grouped buses and direction labels


def build_map_buses(all_buses, in_service_bus_ids, movements): # Prepares the bus data for the frontend map
    output = []

    for bus in all_buses: # skip buses without coordinates, because they can't be placed on the map
        lat = bus.get("lat")
        lon = bus.get("lon")
        if lat is None or lon is None:
            continue

        # Get the bus movement info and ID
        bus_ref = bus.get("vehicle_ref") 
        movement = movements.get(bus_ref, {})

        # Frontend dictionary: Bus ID, destination, service, ETA, progress, coordinates, movements
        output.append({
            "vehicle_ref": bus_ref,
            "destination_name": bus.get("destination_name"),
            "service_type": classify_service(bus),
            "next_stop_name": bus.get("next_stop_name"),
            "arrival_proximity_text": bus.get("arrival_proximity_text"),
            "expected_arrival_time": bus.get("expected_arrival_time"),
            "recorded_at": bus.get("recorded_at"),
            "progress_rate": bus.get("progress_rate"),
            "progress_status": bus.get("progress_status"),
            "lat": lat,
            "lon": lon,
            "is_in_service": bus_ref in in_service_bus_ids,
            "moved_meters": movement.get("moved_meters"),
            "meters_per_minute": movement.get("meters_per_minute"),
            "is_moving_by_position": movement.get("is_moving_by_position"),
        })

    return output


def compute_group_metrics(group_buses, snapshot_time, movements): # Takes a group of buses and computes route-level metrics for that group
    in_service_buses = [bus for bus in group_buses if is_in_service(bus)] # A list of only in-service buses
    in_service_bus_ids = {bus.get("vehicle_ref") for bus in in_service_buses if bus.get("vehicle_ref")} # Build a set of their Bus IDs

    # Count buses that are currently in a layover
    layover_count = sum(
        1 for bus in group_buses
        if is_layover(bus)
    )


    in_service_movements = [ # Get movement info for only in-service buses (that have movement info)
        movements[bus.get("vehicle_ref")]
        for bus in in_service_buses
        if bus.get("vehicle_ref") in movements
    ]

    # Count how many buses are moving vs stationary
    moving_count = sum(1 for movement in in_service_movements if movement.get("is_moving_by_position"))
    stationary_count = sum(1 for movement in in_service_movements if not movement.get("is_moving_by_position"))

    moved_values = [ # Collects distance values for in-service buses
        movement.get("moved_meters")
        for movement in in_service_movements
        if movement.get("moved_meters") is not None
    ]
    speed_values = [ # Collects speed values for in-service buses
        movement.get("meters_per_minute")
        for movement in in_service_movements
        if movement.get("meters_per_minute") is not None
    ]

    # Raw total buses and in-service buses count
    total_buses = len(group_buses)
    in_service_bus_count = len(in_service_buses)

    # Compute the 3 route ratios
    in_service_ratio = round(in_service_bus_count / total_buses, 3) if total_buses > 0 else None

    # Count only buses seen in both snapshots to have enough data for us to measure movement
    movement_eligible_count = len(in_service_movements)
    movement_ratio = (
        round(moving_count / movement_eligible_count, 3)
        if movement_eligible_count > 0
        else None
    )

    layover_ratio = round(layover_count / total_buses, 3) if total_buses > 0 else None

    # Send those values to metrics.py to get subscores, overall score, and health label
    score = compute_route_health(
        total_vehicles=total_buses,
        in_service_vehicles=in_service_bus_count,
        movement_ratio=movement_ratio,
        layover_ratio=layover_ratio,
    )

    # Returns a big raw dictionary containing: counts, ratios, movement, speed, score breakdown, map buses, bus table data
    return {
        "timestamp": snapshot_time,
        "total_vehicles_raw": total_buses,
        "in_service_vehicles_raw": in_service_bus_count,
        "layover_vehicles_raw": layover_count,
        "moving_vehicles_raw": moving_count,
        "stationary_vehicles_raw": stationary_count,
        "in_service_ratio_raw": in_service_ratio,
        "movement_ratio_raw": movement_ratio,
        "layover_ratio_raw": layover_ratio,
        "average_moved_meters_raw": round_or_none(sum(moved_values) / len(moved_values), 2) if moved_values else None,
        "average_meters_per_minute_raw": round_or_none(sum(speed_values) / len(speed_values), 2) if speed_values else None,
        "health_score_raw": score["overall_score"],
        "health_label_raw": score["health_label"],
        "score_breakdown_raw": score,
        "map_vehicles": build_map_buses(group_buses, in_service_bus_ids, movements),
        "vehicles_seen": [
            {
                "vehicle_ref": bus.get("vehicle_ref"),
                "destination_name": bus.get("destination_name"),
                "service_type": classify_service(bus),
                "next_stop_name": bus.get("next_stop_name"),
                "progress_rate": bus.get("progress_rate"),
                "progress_status": bus.get("progress_status"),
                "moved_meters": movements.get(bus.get("vehicle_ref"), {}).get("moved_meters"),
                "meters_per_minute": movements.get(bus.get("vehicle_ref"), {}).get("meters_per_minute"),
                "is_in_service": bus.get("vehicle_ref") in in_service_bus_ids,
            }
            for bus in group_buses
        ],
    }


def smooth_group_metrics(computed_rows): # Smooths a route's history over the last few snapshots
    latest = computed_rows[-1] # Most recent raw route point
    window_rows = computed_rows[-SMOOTHING_WINDOW:] # Last 5 route points, or fewer if history is short

    # Extracts lists of values from that smoothing window (counts and ratios are smoothed using the median)
    total_values = [x["total_vehicles_raw"] for x in window_rows]
    in_service_values = [x["in_service_vehicles_raw"] for x in window_rows]
    layover_values = [x["layover_vehicles_raw"] for x in window_rows]
    moving_values = [x["moving_vehicles_raw"] for x in window_rows]
    stationary_values = [x["stationary_vehicles_raw"] for x in window_rows]

    in_service_ratio_values = [x["in_service_ratio_raw"] for x in window_rows if x["in_service_ratio_raw"] is not None]
    movement_ratio_values = [x["movement_ratio_raw"] for x in window_rows if x["movement_ratio_raw"] is not None]
    layover_ratio_values = [x["layover_ratio_raw"] for x in window_rows if x["layover_ratio_raw"] is not None]
    moved_values = [x["average_moved_meters_raw"] for x in window_rows if x["average_moved_meters_raw"] is not None]
    speed_values = [x["average_meters_per_minute_raw"] for x in window_rows if x["average_meters_per_minute_raw"] is not None]

    # Take the median
    smoothed_total = int(round(median(total_values))) if total_values else latest["total_vehicles_raw"]
    smoothed_in_service = int(round(median(in_service_values))) if in_service_values else latest["in_service_vehicles_raw"]
    smoothed_layover = int(round(median(layover_values))) if layover_values else latest["layover_vehicles_raw"]
    smoothed_moving = int(round(median(moving_values))) if moving_values else latest["moving_vehicles_raw"]
    smoothed_stationary = int(round(median(stationary_values))) if stationary_values else latest["stationary_vehicles_raw"]

    smoothed_in_service_ratio = round_or_none(median_or_none(in_service_ratio_values), 3)
    smoothed_movement_ratio = round_or_none(median_or_none(movement_ratio_values), 3)
    smoothed_layover_ratio = round_or_none(median_or_none(layover_ratio_values), 3)
    smoothed_avg_moved = round_or_none(median_or_none(moved_values), 2)
    smoothed_avg_speed = round_or_none(median_or_none(speed_values), 2)

    # Recompute route health using smoothed values so graphs and smoothed current score use less noisy data
    smoothed_score = compute_route_health(
        total_vehicles=smoothed_total,
        in_service_vehicles=smoothed_in_service,
        movement_ratio=smoothed_movement_ratio,
        layover_ratio=smoothed_layover_ratio,
    )

    # Returns smoothed counts, ratios, averages, health score, etc
    return {
        "timestamp": latest["timestamp"],
        "total_vehicles": smoothed_total,
        "in_service_vehicles": smoothed_in_service,
        "layover_vehicles": smoothed_layover,
        "moving_vehicles": smoothed_moving,
        "stationary_vehicles": smoothed_stationary,
        "in_service_ratio": smoothed_in_service_ratio,
        "movement_ratio": smoothed_movement_ratio,
        "layover_ratio": smoothed_layover_ratio,
        "average_moved_meters": smoothed_avg_moved,
        "average_meters_per_minute": smoothed_avg_speed,
        "health_score": smoothed_score["overall_score"],
        "health_label": smoothed_score["health_label"],
        "score_breakdown": smoothed_score,
        "stability_window_snapshots": len(window_rows),
        "map_vehicles": latest["map_vehicles"],
        "vehicles_seen": latest["vehicles_seen"],
        "raw_latest": {
            "total_vehicles": latest["total_vehicles_raw"],
            "in_service_vehicles": latest["in_service_vehicles_raw"],
            "layover_vehicles": latest["layover_vehicles_raw"],
            "moving_vehicles": latest["moving_vehicles_raw"],
            "stationary_vehicles": latest["stationary_vehicles_raw"],
            "in_service_ratio": latest["in_service_ratio_raw"],
            "movement_ratio": latest["movement_ratio_raw"],
            "layover_ratio": latest["layover_ratio_raw"],
            "average_moved_meters": latest["average_moved_meters_raw"],
            "average_meters_per_minute": latest["average_meters_per_minute_raw"],
            "health_score": latest["health_score_raw"],
            "health_label": latest["health_label_raw"],
            "score_breakdown": latest["score_breakdown_raw"],
        }
    }


def main():
    rows = load_jsonl(RAW_PATH) # Load raw snapshots

    if not rows: # don't process data without snapshots first
        print("No raw bus data found. Run fetch_data.py first.")
        return

    recent_rows = rows[-MAX_HISTORY_POINTS:] # Only keep the most recent (180 snapshots)

    computed_by_route = {} # raw computed history for each route key
    route_index = {} # stores a borough-to-route lookup used by the frontend
    all_direction_labels = {} # stores direction labels by route key

    previous = None
    for snapshot in recent_rows: # look through snapshots in order and skip any malformed snapshot that doesn't have a bus list
        if snapshot.get("buses") is None:
            continue
 
        movements = compute_bus_movements(snapshot, previous) # compute bus movements relative to previous snapshot
        grouped, direction_labels = build_route_groups(snapshot) # group buses into route groups
        all_direction_labels.update(direction_labels) # save direction labels

        for route_key, group_buses in grouped.items(): # unpack the route key and add the route into the borough index
            borough, route_short_name, direction = route_key.split("|")
            route_index.setdefault(borough, set()).add(route_short_name) # Create an empty set if the borough key doesn't exist yet

           # Compute raw metrics for that route group at this snapshot time, then append that point into the route’s history list
            point = compute_group_metrics(group_buses, snapshot.get("snapshot_time"), movements)
            computed_by_route.setdefault(route_key, []).append(point)

        previous = snapshot

    routes_output = {} # Final routes object

    # For each route, compute its smoothed current state from its history
    for route_key, computed_rows in computed_by_route.items():
        current = smooth_group_metrics(computed_rows)

        # Build a full smoothed history over time
        history = []
        for i in range(len(computed_rows)):
            smoothed = smooth_group_metrics(computed_rows[: i + 1])
            history.append({
                "timestamp": smoothed["timestamp"],
                "total_vehicles": smoothed["total_vehicles"],
                "in_service_vehicles": smoothed["in_service_vehicles"],
                "layover_vehicles": smoothed["layover_vehicles"],
                "moving_vehicles": smoothed["moving_vehicles"],
                "stationary_vehicles": smoothed["stationary_vehicles"],
                "in_service_ratio": smoothed["in_service_ratio"],
                "movement_ratio": smoothed["movement_ratio"],
                "layover_ratio": smoothed["layover_ratio"],
                "average_moved_meters": smoothed["average_moved_meters"],
                "average_meters_per_minute": smoothed["average_meters_per_minute"],
                "health_score": smoothed["health_score"],
            })

        borough, route_short_name, direction = route_key.split("|") 

        # final route object
        routes_output[route_key] = {
            "borough": borough,
            "route": route_short_name,
            "direction": direction,
            "direction_label": all_direction_labels.get(route_key, "Unknown"),
            "current": current,
            "history": history,
        }
    output = { # final JSON structure the frontend loads: timestamp, route index, routes
        "updated_at": recent_rows[-1]["snapshot_time"] if recent_rows else None,
        "route_index": {k: sorted(v) for k, v in route_index.items()},
        "routes": routes_output,
    }

    # Writes the JSON to a temporary file first for safety
    temp_output_path = OUTPUT_PATH + ".tmp"
    with open(temp_output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    os.replace(temp_output_path, OUTPUT_PATH) # replace the old output with the new  one

    print(f"Wrote {OUTPUT_PATH}")
    print(f"Processed {len(routes_output)} live route groups")


if __name__ == "__main__": # Run file after execution
    while True:
        try:
            main()
        except Exception as e:
            print(f"processing error: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)

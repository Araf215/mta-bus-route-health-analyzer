# MTA Bus Route Health Analyzer

## Overview
MTA Bus Route Health Analyzer is a transit data project that uses the MTA Bus Time API to collect live bus snapshots, process route-level service metrics, and visualize route activity through an interactive dashboard. I built this project to turn messy real-time transit data into something more readable and interpretable through route filtering, movement analysis, layover detection, charts, and live map views.

## Problem
Although the MTA is widely recognized as one of the nation’s most important transit systems because it serves millions of riders and operates 24/7, its bus network is often criticized for being among the slowest in the country. MTA buses face recurring challenges such as delays, traffic congestion, service gaps, and maintenance-related disruptions. This project was created to examine one part of that broader problem: route health. In other words, it asks whether a particular bus route is effectively fulfilling its purpose of serving riders in a timely and active way.

## Solution
This project addresses that problem by using the MTA Bus Time API to collect live bus data and convert it into a more readable route health dashboard. It processes raw vehicle snapshots, groups buses by route and direction, and calculates route-level metrics such as in-service ratio, movement ratio, and layover ratio. By displaying these results through charts, maps, and route summaries, the project makes it easier to understand how actively a bus route appears to be serving riders in real time.

## Key Features
- Collects live MTA bus snapshots from the Vehicle Monitoring API
- Filters routes by borough, route, and direction
- Uses destination-based direction labels to make route selection more readable
- Computes route-level metrics such as in-service ratio, movement ratio, and layover ratio
- Generates a custom route health score from live activity data
- Estimates bus movement between snapshots using changes in geographic position (haversine formula)
- Displays recent route trends through charts (smoothed)
- Visualizes active buses on an interactive map
- Shows route-level vehicle details in a live data table
- Treats service variants like `M15` and `M15-SBS` as distinct routes

## Tech Stack
- **Python** for backend data collection, route processing, and metric computation
- **JavaScript** for frontend dashboard logic and interactive updates
- **HTML/CSS** for the structure and styling of the user interface
- **MTA Bus Time API** as the live transit data source
- **Chart.js** for time-series route visualizations
- **Leaflet.js** for live geographic bus mapping
- **JSONL** for timestamped raw data storage
- **JSON** for processed route-level output consumed by the frontend
- **requests** for HTTP API access
- **python-dotenv** for configuration through environment variables

## How It Works
1. `fetch.py` requests live vehicle monitoring data from the MTA Bus Time API at a fixed interval.
2. Raw bus snapshots are stored in JSONL format in `data/raw/`.
3. `process.py` reads recent snapshots, compares consecutive bus positions, estimates movement, groups buses by borough, route, and direction, and computes route-level metrics.
4. `metrics.py` converts those route-level ratios into interpretable subscores and an overall route health score.
5. Processed output is written to `data/processed/all_routes_live_metrics.json`.
6. `app.js` loads the processed JSON and renders the dashboard with filters, summary cards, charts, a live map, and a route-level vehicle table.

## Project Structure
data/
  raw/
    all_buses_snapshots.jsonl
  processed/
    all_routes_live_metrics.json
fetch.py
process.py
metrics.py
app.js
index.html
style.css
README.md

## Route Metrics
**In-Service Ratio**
The proportion of detected buses on a route that appear to be actively serving riders rather than sitting in layover or no-progress states.

**Movement Ratio**
The proportion of in-service buses that appear to be moving between snapshots (every 1 min). Movement is estimated from changes in latitude and longitude positions rather than the official schedule.

**Layover Ratio**
The proportion of detected buses that appear inactive or in layover.

## Design Decisions
- **Ratios over raw counts**: Each bus route has different ridership levels, route lengths, and service patterns. Instead of using a fixed baseline that applies equally to every route, such as treating 10 buses as automatically equal to a 10/10 score, I chose to use ratios because proportions are more meaningful and more comparable across different routes than raw counts alone.
- **Scoring**: Due to limitations in the API, bunching and headway metrics could not be implemented reliably in this project. Instead, I chose to use layover activity, movement, and in-service counts as the main metrics because those data points were provided more consistently.
- **Movement Calculation**: Bus movement is inferred from changes in latitude and longitude coordinates provided by the API. I used the Haversine formula to estimate the geographic distance traveled between snapshots.
- **Smoothed vs. Raw Data**: I decided that current route snapshots, the live route map, and route scoring should use raw data so the dashboard reflects the most up-to-date route conditions possible. However, because the API can introduce short-term noise, such as sudden vehicle count spikes or incorrect movement readings, I chose to smooth the graph data so that broader trends are easier to interpret.

## Limitations
- The MTA API can contain incomplete, inconsistent, or noisy real-time data.
- Movement is estimated from geographic position changes, not official schedule data or dispatch records.
- Destination strings may vary slightly, which can create minor inconsistencies in direction grouping.
- The route health score is a heuristic metric, not an official transit performance standard.
- Some important service conditions, such as true bunching or schedule adherence, are difficult to measure reliably from the live feed alone.
## Future Improvements
- If the MTA begins providing reliable data for key metrics such as headways and bunching, I plan to incorporate those metrics into the project.
- I plan to add route lines to the live route map, although this may be difficult because it would require obtaining GeoJSON data and route paths can change over time. I am still deciding whether that investment is worth it.
- If I am able to get feedback from knowledgeable transit hobbyists, data analysts, or other experts in this area, I may revise and strengthen the project’s metrics accordingly.
## Local Setup
1. Clone the repository.
2. Install dependencies with `pip install -r requirements.txt`. If you want, you can also create and activate a Python virtual environment first.
3. Create a `.env` file based on the example file and add your MTA API key.
4. Run the data collection script: `python fetch.py`
5. After the first raw snapshot is written to `data/raw/`, run the data processing script in a separate terminal: `python process.py`
6. Start a local server with `python -m http.server 8000`
7. Open `http://localhost:8000` in your browser.
## Open Source Status
This project is open source and available for others to explore, learn from, and build on. If you use or reference this project, please provide credit to my GitHub: `Araf215`.
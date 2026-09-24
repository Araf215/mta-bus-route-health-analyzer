# Development Log

## 09/24/2026

### Open Source Pipeline and Frontend Update

This update focused on fixing the local open source workflow and bringing the dashboard closer to the live version.

#### Changes

- Fixed the Python file paths so raw and processed data always go into the main `data/` folder instead of accidentally creating folders inside `scripts/`.

- Changed the local version to use 5-minute snapshots and made the collector, processor, frontend refresh, and charts use the same timing.

- Updated the frontend to load the processed JSON directly from `data/processed/all_routes_live_metrics.json`.

- Brought the open source dashboard closer to the live site's layout, wording, and metric descriptions.

- Made the current route info use the latest snapshot while keeping the trend charts smoothed.

- Cleaned up the route status and movement-ratio wording so it matches what the backend is actually calculating.

- Updated the setup instructions to match the current folder structure and script names.

### Files Updated

- `scripts/fetch_data.py`
- `scripts/process_data.py`
- `web/app.js`
- `web/index.html`
- `README.md`


## 09/22/2026

### Route Health Metric Fixes

This update focused on improving the accuracy of the route health metrics.

#### Changes

- Changed service classification so only buses in layover are counted as out of service.
- Changed movement detection to use meters per minute instead of just distance moved.
- Fixed the movement ratio so it only uses buses that actually have enough data to compare movement.
- Updated the frontend to use the movement ratio calculated by the backend.
- Cleaned up the dashboard timing and wording so it matches how the backend works.

### Files Updated

- `scripts/process_data.py`
- `web/app.js`
- `web/index.html`

### Notes

The overall route health scoring weights were not changed. These updates focused on improving the data used by the existing scoring model.

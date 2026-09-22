# Development Log

## 09/22/2026

### Route Health Metric Fixes

This update focused on making the route health calculations more accurate and keeping the open source version aligned with the live project.

#### Changes

- Updated how buses are classified as in service.
  - Buses marked as `noProgress` are no longer automatically treated as out of service.
  - Only buses identified as being in a layover are excluded from active service.

- Improved movement detection.
  - Movement is now based on meters per minute instead of a fixed distance between snapshots.
  - This makes the calculation more consistent if the polling interval changes.

- Fixed the movement ratio calculation.
  - The movement ratio now only uses buses that have enough data to compare their current and previous positions.
  - Buses without a previous snapshot are no longer indirectly treated as stationary.

- Updated the frontend to use the movement ratio calculated by the backend instead of recalculating it differently in the browser.

- Updated the time frontend values and wording to better match the current data collection interval.

### Files Updated

- `scripts/process_data.py`
- `public/app.js`
- `public/index.html`

### Notes

These changes do not alter the overall route health scoring weights. The goal of this update was to improve the accuracy of the data being fed into the existing scoring system rather than redesign the scoring model. There will be future updates utilizing updated MTA API features to help create a better scoring model.

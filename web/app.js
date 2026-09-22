// Chart + map objects
// Reuse chart and map instances between refreshes so rerenders stay clean.
let trendChart;
let movementChart;
let liveMap;
let mapMarkersLayer;

// Frontend refresh timing should match backend timing.
// The page pulls new processed data every 5 minutes.
// The charts display the last 30 minutes in 5-minute buckets.
const REFRESH_MS = 300000;        // 5 minutes
const CHART_WINDOW_MINUTES = 30;  // show last 30 minutes
const CHART_BUCKET_MINUTES = 5;   // one bucket per backend update

// These should match the backend configuration.
// A bus is only counted as moving if it reaches this movement rate.
// Current displayed values are smoothed using this many recent snapshots.
const BACKEND_POLL_INTERVAL_MINUTES = 10;
const MOVING_THRESHOLD_METERS_PER_MINUTE = 25;
const SMOOTHING_WINDOW_SNAPSHOTS = 5;

// Shared UI colors so charts, cards, and map markers stay consistent.
const UI = {
  colors: {
    primary: "#69b86f",
    primaryDark: "#4f9f59",
    soft: "rgba(105, 184, 111, 0.14)",
    border: "#dfeee1",
    text: "#18231b",
    muted: "#5f6f63",
    local: "#4f9f59",
    layover: "#9aa79c",
    stationary: "#d8a23d"
  }
};

// Request tracking prevents older fetches from overwriting newer ones.
let latestRequestId = 0;
let refreshTimer = null;

// Shared live dataset for all routes.
// selectedRouteKey stores the currently selected borough|route|direction key.
let allRoutesData = null;
let selectedRouteKey = null;

// Fetch the processed backend JSON.
// no-store avoids browser caching so the dashboard always uses fresh data.
async function loadLiveMetrics() {
  const response = await fetch("/api/data", { cache: "no-store" });

  if (!response.ok) {
    throw new Error("Processed live metrics file not found.");
  }

  return response.json();
}

// Format timestamps for cards, popups, and chart-adjacent labels.
function formatTimeLabel(iso, includeSeconds = false) {
  if (!iso) return "N/A";

  const dt = new Date(iso);
  return dt.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {})
  });
}

// Generic decimal formatter for scores and averages.
function formatNumber(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return Number(value).toFixed(digits);
}

// Whole-number formatter for vehicle counts.
function formatInteger(value) {
  if (value == null || Number.isNaN(Number(value))) return "--";
  return String(Math.round(Number(value)));
}

// Convert a ratio like 0.82 into 82.0%.
function formatPercentFromRatio(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

// Distance display helper.
function formatMeters(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return `${Number(value).toFixed(1)} m`;
}

// Movement-rate display helper.
function formatMetersPerMinute(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return `${Number(value).toFixed(1)} m/min`;
}

// Make route and direction labels easier to read.
// Example: to_south_ferry -> To South Ferry
function titleCase(value) {
  if (!value) return "N/A";
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, char => char.toUpperCase());
}

// Prefer the backend direction label when available.
function getRouteDirectionLabel(routeData) {
  return routeData?.direction_label || titleCase(routeData?.direction || "unknown");
}

// Get the current snapshot timestamp for the selected route.
function getLatestSnapshotTime(routeData) {
  return routeData?.current?.timestamp ? new Date(routeData.current.timestamp) : null;
}

// Build the rolling chart window from saved history.
// This uses the chart history array, not the smoothing buffer.
// Missing buckets borrow the last seen point so the charts stay visually continuous.
function getRollingWindowPoints(history, currentTimestamp) {
  if (!Array.isArray(history) || !currentTimestamp) return [];

  const end = new Date(currentTimestamp);
  end.setSeconds(0, 0);

  const minute = end.getMinutes();
  end.setMinutes(minute - (minute % CHART_BUCKET_MINUTES));

  const start = new Date(end.getTime() - CHART_WINDOW_MINUTES * 60 * 1000);

  const filtered = history
    .filter(point => point.timestamp)
    .map(point => {
      const dt = new Date(point.timestamp);
      dt.setSeconds(0, 0);

      const bucketMinute = dt.getMinutes() - (dt.getMinutes() % CHART_BUCKET_MINUTES);
      dt.setMinutes(bucketMinute);

      return {
        ...point,
        roundedTime: dt
      };
    })
    .filter(point => point.roundedTime >= start && point.roundedTime <= end);

  const buckets = [];
  for (let i = CHART_WINDOW_MINUTES; i >= 0; i -= CHART_BUCKET_MINUTES) {
    const bucketTime = new Date(end.getTime() - i * 60 * 1000);
    buckets.push({
      bucketTime,
      label: bucketTime.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      point: null
    });
  }

  for (const point of filtered) {
    const bucket = buckets.find(b => b.bucketTime.getTime() === point.roundedTime.getTime());
    if (bucket) {
      bucket.point = point;
    }
  }

  let lastSeen = null;
  return buckets.map(bucket => {
    if (bucket.point) lastSeen = bucket.point;
    return {
      label: bucket.label,
      point: lastSeen
    };
  });
}

// The backend may expose raw_latest for the unsmoothed latest snapshot.
// If it exists, use it for direct count-based UI calculations.
function getLiveSnapshot(current) {
  return current.raw_latest || current;
}

// Recalculate ratios directly from the current counts shown by the backend.
// This keeps the frontend display consistent even if precomputed ratios are absent.
function getCurrentDerivedMetrics(current) {
  const live = getLiveSnapshot(current);

  const totalBuses = Number(live.total_vehicles ?? 0);
  const inServiceBuses = Number(live.in_service_vehicles ?? 0);
  const layoverBuses = Number(live.layover_vehicles ?? 0);

  const inServiceRatio =
    live.in_service_ratio != null
      ? Number(live.in_service_ratio)
      : totalBuses > 0
        ? inServiceBuses / totalBuses
        : null;

  const movementRatio =
    live.movement_ratio != null
      ? Number(live.movement_ratio)
      : null;

  const layoverRatio =
    live.layover_ratio != null
      ? Number(live.layover_ratio)
      : totalBuses > 0
        ? layoverBuses / totalBuses
        : null;

  return {
    ...live,
    in_service_ratio: inServiceRatio,
    movement_ratio: movementRatio,
    layover_ratio: layoverRatio
  };
}

// Get every available borough|route|direction key from the loaded dataset.
function getAllRouteKeys() {
  return Object.keys(allRoutesData?.routes || {}).sort();
}

// Build the borough, route, and direction dropdowns.
// The selections cascade in order: borough -> route -> direction.
function populateFilters() {
  const boroughSelect = document.getElementById("boroughSelect");
  const routeSelect = document.getElementById("routeSelect");
  const directionSelect = document.getElementById("directionSelect");

  const routeKeys = getAllRouteKeys();
  if (!routeKeys.length) return;

  const boroughs = [...new Set(routeKeys.map(key => key.split("|")[0]))].sort();
  boroughSelect.innerHTML = boroughs
    .map(borough => `<option value="${borough}">${borough}</option>`)
    .join("");

  // Rebuild the route list whenever the borough changes.
  function updateRouteOptions() {
    const borough = boroughSelect.value;

    const routes = [...new Set(
      routeKeys
        .filter(key => key.split("|")[0] === borough)
        .map(key => key.split("|")[1])
    )].sort();

    routeSelect.innerHTML = routes
      .map(route => `<option value="${route}">${route}</option>`)
      .join("");

    updateDirectionOptions();
  }

  // Rebuild the direction list whenever the route changes.
  function updateDirectionOptions() {
    const borough = boroughSelect.value;
    const route = routeSelect.value;

    const matchingKeys = routeKeys.filter(key => {
      const [b, r] = key.split("|");
      return b === borough && r === route;
    });

    const directionOptions = matchingKeys
      .map(key => {
        const routeData = allRoutesData.routes[key];
        const direction = key.split("|")[2];
        const label = routeData?.direction_label || titleCase(direction);

        return {
          value: direction,
          label
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label));

    directionSelect.innerHTML = directionOptions
      .map(option => `<option value="${option.value}">${option.label}</option>`)
      .join("");

    updateSelectedRoute();
  }

  // Store the currently selected route key, then rerender the page.
  function updateSelectedRoute() {
    selectedRouteKey = [
      boroughSelect.value,
      routeSelect.value,
      directionSelect.value
    ].join("|");

    renderSelectedRoute();
  }

  boroughSelect.addEventListener("change", updateRouteOptions);
  routeSelect.addEventListener("change", updateDirectionOptions);
  directionSelect.addEventListener("change", updateSelectedRoute);

  updateRouteOptions();
}

// Main summary box for the selected route.
// This exposes the movement threshold and smoothing settings clearly.
function renderScoreBox(routeData) {
  const live = getCurrentDerivedMetrics(routeData.current);

  document.getElementById("scoreBox").innerHTML = `
    <div class="score-line"><strong>Route:</strong> ${routeData.route} ${getRouteDirectionLabel(routeData)}</div>
    <div class="score-line"><strong>Borough:</strong> ${routeData.borough}</div>
    <div class="score-line"><strong>Route Health:</strong> ${formatNumber(routeData.current.health_score, 1)} / 10 (${routeData.current.health_label})</div>
    <div class="score-line"><strong>Total Buses:</strong> ${formatInteger(live.total_vehicles)}</div>
    <div class="score-line"><strong>In-Service Buses:</strong> ${formatInteger(live.in_service_vehicles)}</div>
    <div class="score-line"><strong>Layover Buses:</strong> ${formatInteger(live.layover_vehicles)}</div>
    <div class="score-line"><strong>Moving Buses:</strong> ${formatInteger(live.moving_vehicles)}</div>
    <div class="score-line"><strong>Movement Ratio:</strong> ${formatPercentFromRatio(live.movement_ratio)}</div>
    <div class="score-line"><strong>Average Movement:</strong> ${formatMeters(live.average_moved_meters)}</div>
    <div class="score-line"><strong>Average Speed:</strong> ${formatMetersPerMinute(live.average_meters_per_minute)}</div>
    <div class="score-line"><strong>Moving Threshold:</strong> A bus counts as moving at ${MOVING_THRESHOLD_METERS_PER_MINUTE} m/min or higher</div>
    <div class="score-line"><strong>Smoothing:</strong> Median of the last ${SMOOTHING_WINDOW_SNAPSHOTS} backend snapshots</div>
    <div class="score-line"><strong>Last Updated:</strong> ${formatTimeLabel(routeData.current.timestamp, true)}</div>
    <div class="score-line"><strong>Update Frequency:</strong> Every ${BACKEND_POLL_INTERVAL_MINUTES} minutes</div>
  `;
}

// Small stat cards beneath the main score box.
function renderMiniStats(current) {
  const live = getCurrentDerivedMetrics(current);

  document.getElementById("totalBusesStat").textContent = formatInteger(live.total_vehicles);
  document.getElementById("inServiceStat").textContent = formatInteger(live.in_service_vehicles);
  document.getElementById("layoverStat").textContent = formatInteger(live.layover_vehicles);
  document.getElementById("movingStat").textContent = formatInteger(live.moving_vehicles);
  document.getElementById("movementRatioStat").textContent = formatPercentFromRatio(live.movement_ratio);
  document.getElementById("avgMoveStat").textContent = formatMeters(live.average_moved_meters);
  document.getElementById("avgSpeedStat").textContent = formatMetersPerMinute(live.average_meters_per_minute);
}

// Plain-language status notes for the selected route.
function renderSystemStatus(current) {
  const live = getCurrentDerivedMetrics(current);
  const container = document.getElementById("systemStatus");
  const notes = [];

  notes.push(`Health score is currently rated as ${current.health_label}, with a Route Health score of ${formatNumber(current.health_score, 1)} out of 10.`);

  if (live.total_vehicles != null && live.in_service_vehicles != null) {
    notes.push(`${formatInteger(live.in_service_vehicles)} of ${formatInteger(live.total_vehicles)} detected buses are currently being counted as in service.`);
  }

  if (live.layover_vehicles != null) {
    notes.push(`${formatInteger(live.layover_vehicles)} buses are currently flagged as layover or no-progress.`);
  }

  if (live.moving_vehicles != null && live.in_service_vehicles != null) {
    notes.push(`${formatInteger(live.moving_vehicles)} of ${formatInteger(live.in_service_vehicles)} in-service buses are currently counted as moving using a ${MOVING_THRESHOLD_METERS_PER_MINUTE} m/min threshold.`);
  }

  if (live.average_moved_meters != null) {
    notes.push(`The average detected bus moved about ${formatMeters(live.average_moved_meters)} between recent snapshots.`);
  }
  container.innerHTML = notes.map(note => `<div class="status-pill">${note}</div>`).join("");
}

// Explain how the route health score is calculated.
// This is intentionally more transparent about both formulas and assumptions.
function renderScoreBreakdown(current) {
  const score = current.score_breakdown || {};
  const weights = score.weights || {};
  const container = document.getElementById("scoreBreakdown");

  const inServiceWeight = Math.round((weights.in_service_ratio ?? 0.6) * 100);
  const movementWeight = Math.round((weights.movement_ratio ?? 0.3) * 100);
  const layoverWeight = Math.round((weights.layover_ratio ?? 0.1) * 100);

  const inServiceScore = formatNumber(score.in_service_score, 1);
  const movementScore = formatNumber(score.movement_score, 1);
  const layoverScore = formatNumber(score.layover_score, 1);
  const overallScore = formatNumber(score.overall_score, 1);

  container.innerHTML = `
    <div class="metric-pill">
      <strong>${inServiceWeight}% In-Service Ratio</strong><br>
      Shows how many detected buses are actively serving the route right now.<br><br>
      <strong>Formula</strong><br>
      In-Service Ratio = In-Service Buses / Total Detected Buses<br>
      In-Service Score = In-Service Ratio × 10
    </div>
    <div class="metric-pill">
      <strong>${movementWeight}% Movement Ratio</strong><br>
      Shows how many in-service buses appear to be progressing between snapshots.<br><br>
      <strong>When is a bus counted as moving?</strong><br>
      A bus is counted as moving only if its estimated movement rate is at least ${MOVING_THRESHOLD_METERS_PER_MINUTE} meters per minute between snapshots.<br><br>
      <strong>Formula</strong><br>
      Movement Ratio = Moving In-Service Buses / In-Service Buses<br>
      Movement Score = Movement Ratio × 10
    </div>
    <div class="metric-pill">
      <strong>${layoverWeight}% Layover Ratio</strong><br>
      Reflects how many detected buses are inactive at that moment.<br><br>
      <strong>Formula</strong><br>
      Layover Ratio = Layover Buses / Total Detected Buses<br>
      Layover Score = 10 - (Layover Ratio × 10)
    </div>
    <div class="metric-pill">
      <strong>Current Score Breakdown</strong><br>
      In-Service Score: ${inServiceScore}<br>
      Movement Score: ${movementScore}<br>
      Layover Score: ${layoverScore}<br>
      Overall Route Health: ${overallScore} / 10<br><br>
      <strong>Data note</strong><br>
      Current route snapshot values are based on the latest available snapshot, while the graphs use the median of the last ${SMOOTHING_WINDOW_SNAPSHOTS} backend snapshots to smooth short-term API noise. </div>
  `;
}

// Chart bus counts across the selected time window.
function createTrendChart(historyWindow) {
  const ctx = document.getElementById("trendChart").getContext("2d");

  if (trendChart) trendChart.destroy();

  const labels = historyWindow.map(x => x.label);

  trendChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Total Buses",
          data: historyWindow.map(x => x.point?.total_vehicles ?? null),
          borderColor: UI.colors.primaryDark,
          borderWidth: 3,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false
        },
        {
          label: "In-Service Buses",
          data: historyWindow.map(x => x.point?.in_service_vehicles ?? null),
          borderColor: "#2f7d46",
          borderWidth: 3,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false
        },
        {
          label: "Moving Buses",
          data: historyWindow.map(x => x.point?.moving_vehicles ?? null),
          borderColor: "#d8a23d",
          borderWidth: 3,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false
        },
        {
          label: "Layover Buses",
          data: historyWindow.map(x => x.point?.layover_vehicles ?? null),
          borderColor: "#9aa79c",
          borderWidth: 3,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          labels: {
            color: UI.colors.text,
            boxWidth: 12,
            usePointStyle: true
          }
        }
      },
      scales: {
        x: {
          title: {
            display: true,
            text: `Time (Last ${CHART_WINDOW_MINUTES} Minutes)`,
            color: UI.colors.text,
            font: { weight: "700" }
          },
          ticks: {
            color: UI.colors.muted,
            autoSkip: false
          },
          grid: {
            color: UI.colors.border
          }
        },
        y: {
          beginAtZero: true,
          title: {
            display: true,
            text: "Number of Buses",
            color: UI.colors.text,
            font: { weight: "700" }
          },
          ticks: {
            color: UI.colors.muted,
            precision: 0
          },
          grid: {
            color: UI.colors.border
          }
        }
      }
    }
  });
}

// Chart movement ratio alongside average distance moved.
function createMovementChart(historyWindow) {
  const ctx = document.getElementById("movementChart").getContext("2d");

  if (movementChart) movementChart.destroy();

  const labels = historyWindow.map(x => x.label);

  movementChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Movement Ratio",
          data: historyWindow.map(x =>
            x.point?.movement_ratio == null ? null : Number(x.point.movement_ratio) * 100
          ),
          borderColor: UI.colors.primaryDark,
          borderWidth: 3,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false,
          yAxisID: "yPercent"
        },
        {
          label: "Avg Moved (m)",
          data: historyWindow.map(x => x.point?.average_moved_meters ?? null),
          borderColor: "#2f7d46",
          borderWidth: 3,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false,
          yAxisID: "yMeters"
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          labels: {
            color: UI.colors.text,
            boxWidth: 12,
            usePointStyle: true
          }
        }
      },
      scales: {
        x: {
          title: {
            display: true,
            text: `Time (Last ${CHART_WINDOW_MINUTES} Minutes)`,
            color: UI.colors.text,
            font: { weight: "700" }
          },
          ticks: {
            color: UI.colors.muted,
            autoSkip: false
          },
          grid: {
            color: UI.colors.border
          }
        },
        yPercent: {
          type: "linear",
          position: "left",
          beginAtZero: true,
          max: 100,
          title: {
            display: true,
            text: "Percent Moving",
            color: UI.colors.text,
            font: { weight: "700" }
          },
          ticks: {
            color: UI.colors.muted,
            callback: value => `${value}%`
          },
          grid: {
            color: UI.colors.border
          }
        },
        yMeters: {
          type: "linear",
          position: "right",
          beginAtZero: true,
          title: {
            display: true,
            text: "Average Distance Moved (m)",
            color: UI.colors.text,
            font: { weight: "700" }
          },
          ticks: {
            color: UI.colors.muted
          },
          grid: {
            drawOnChartArea: false
          }
        }
      }
    }
  });
}

// Create the Leaflet map only once.
function initializeMap() {
  if (liveMap) return;

  liveMap = L.map("liveMap").setView([40.76, -73.96], 12);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(liveMap);

  mapMarkersLayer = L.layerGroup().addTo(liveMap);
}

// Choose map marker colors based on the bus state.
// Gray = out of service / layover
// Green = moving in service
// Yellow = in service but not counted as moving
function getMarkerStyle(bus) {
  if (!bus.is_in_service) {
    return {
      color: UI.colors.layover,
      fillColor: "#d5ddd6"
    };
  }

  if (bus.is_moving_by_position) {
    return {
      color: UI.colors.local,
      fillColor: "#69b86f"
    };
  }

  return {
    color: UI.colors.stationary,
    fillColor: "#f3c96a"
  };
}

// Render live bus markers for the selected route.
function renderMapBuses(current) {
  initializeMap();
  mapMarkersLayer.clearLayers();

  const buses = current.map_vehicles || [];
  const latLngs = [];

  buses.forEach(bus => {
    const lat = Number(bus.lat);
    const lon = Number(bus.lon);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    latLngs.push([lat, lon]);

    const style = getMarkerStyle(bus);

    const marker = L.circleMarker([lat, lon], {
      radius: 8,
      color: style.color,
      weight: 2,
      fillColor: style.fillColor,
      fillOpacity: 0.95
    });

    marker.bindPopup(`
      <strong>${bus.vehicle_ref || "Unknown bus"}</strong><br>
      Service: ${titleCase(bus.service_type)}<br>
      Destination: ${bus.destination_name || "N/A"}<br>
      Next stop: ${bus.next_stop_name || "N/A"}<br>
      Proximity: ${bus.arrival_proximity_text || "N/A"}<br>
      In service: ${bus.is_in_service ? "Yes" : "No"}<br>
      Position movement: ${formatMeters(bus.moved_meters)}<br>
      Speed estimate: ${formatMetersPerMinute(bus.meters_per_minute)}<br>
      Counted as moving: ${bus.is_moving_by_position ? `Yes (>= ${MOVING_THRESHOLD_METERS_PER_MINUTE} m/min)` : `No (< ${MOVING_THRESHOLD_METERS_PER_MINUTE} m/min or insufficient data)`}
    `);

    marker.addTo(mapMarkersLayer);
  });

  if (latLngs.length === 1) {
    liveMap.setView(latLngs[0], 14);
  } else if (latLngs.length > 1) {
    liveMap.fitBounds(latLngs, { padding: [28, 28] });
  }

  // Fix map sizing after DOM updates.
  setTimeout(() => {
    liveMap.invalidateSize();
  }, 100);
}

// Build the live bus table for the selected route.
function renderBusTable(current) {
  const buses = current.vehicles_seen || [];
  const body = document.getElementById("busTableBody");

  if (!buses.length) {
    body.innerHTML = `<tr><td colspan="6">No live buses found.</td></tr>`;
    return;
  }

  body.innerHTML = buses.map(bus => `
    <tr>
      <td>${bus.vehicle_ref ?? "N/A"}</td>
      <td>${bus.next_stop_name ?? "N/A"}</td>
      <td>${titleCase(bus.service_type)}</td>
      <td>${bus.is_in_service ? "Yes" : "No"}</td>
      <td>${formatMeters(bus.moved_meters)}</td>
      <td>${formatMetersPerMinute(bus.meters_per_minute)}</td>
    </tr>
  `).join("");
}

// Static project problem/solution text.
function renderProblemSolutionText() {
  document.getElementById("problemText").innerHTML = `
    MTA bus service is often criticized for being slow and inconsistent, but the live data behind it is not very easy to read on its own.
    Raw API responses can be messy, technical, and hard for an average person to interpret quickly, especially when trying to understand what is happening on a route in real time.
  `;

  document.getElementById("solutionText").innerHTML = `
    This project uses the MTA API to turn raw live bus data into a route health dashboard that is clearer and easier to visualize.
    By tracking signals like in-service ratio, movement ratio, and layover ratio, it makes the data easier to understand while also showing how messy real-world data can be collected, processed, and turned into something useful.
    The scores and route health labels should still be taken with a grain of salt, since I am not a transit professional and this is a personal engineering and data analysis project, not an official service benchmark.
  `;
}

// Static method text.
// This now explicitly explains the movement threshold and smoothing basis.
function renderMethodText() {
  document.getElementById("methodText").innerHTML = `
   This project works by collecting a live snapshot of MTA bus data from the MTA API every ${BACKEND_POLL_INTERVAL_MINUTES} minutes.
   I chose a ${BACKEND_POLL_INTERVAL_MINUTES}-minute polling interval to reduce backend load and make lightweight deployment more practical while still preserving useful live route trends.
   After each snapshot is fetched, the data is processed to identify the buses on the selected route,
   determine which ones appear to be in service, estimate how many are moving between snapshots,
   and calculate the custom route health metrics used in the dashboard.
   A bus is currently counted as moving only if its estimated movement rate is at least ${MOVING_THRESHOLD_METERS_PER_MINUTE} meters per minute between snapshots.
   The site then displays the current route snapshot, the live route map using Leaflet.js, the score breakdown, and the other route details based on that processed data.
   To reduce short-term API noise, the displayed current metrics are smoothed using the median of the last ${SMOOTHING_WINDOW_SNAPSHOTS} processed snapshots rather than the full chart history.
  `;
}

// Static limitations text.
function renderLimitsText() {
  document.getElementById("limitsText").innerHTML = `
    The metrics used in this project are not the strongest possible measures of route health.
    Bunching and headways would likely be more meaningful, but the MTA API often returned missing or unreliable values when I attempted to use them.
    That is more likely a limitation of the live feed than proof that those conditions rarely occur.
    Because of that, I focused on the signals the API provided more consistently: in-service count, movement count, and layover count.
    These metrics are meant to capture how actively buses on a route appear to be serving riders in real time,
    without depending on official schedule comparisons. Since each route is different in length, ridership, and service pattern,
    I relied on ratios than raw counts so the results are more comparable across routes.
    The movement threshold and smoothing choices are custom project settings, so they should be understood as transparent assumptions rather than official MTA standards.
  `;
}

// Render every UI section for the selected route.
function renderSelectedRoute() {
  if (!allRoutesData || !selectedRouteKey || !allRoutesData.routes[selectedRouteKey]) return;

  const routeData = allRoutesData.routes[selectedRouteKey];
  const current = routeData.current;
  const currentTimestamp = getLatestSnapshotTime(routeData);
  const historyWindow = getRollingWindowPoints(routeData.history || [], currentTimestamp);

  renderScoreBox(routeData);
  renderMiniStats(current);
  renderSystemStatus(current);
  renderScoreBreakdown(current);
  renderProblemSolutionText();
  renderMethodText();
  renderLimitsText();
  createTrendChart(historyWindow);
  createMovementChart(historyWindow);
  renderMapBuses(current);
  renderBusTable(current);
}

// Pull the newest backend data and rerender the dashboard.
async function refreshDashboard() {
  const requestId = ++latestRequestId;

  try {
    const data = await loadLiveMetrics();

    if (requestId !== latestRequestId) return;

    allRoutesData = data;

    // First load creates the dropdowns.
    // Later refreshes keep the same selection and simply rerender the page.
    if (!selectedRouteKey) {
      populateFilters();
    } else {
      renderSelectedRoute();
    }
  } catch (err) {
    if (requestId !== latestRequestId) return;

    console.error(err);
    document.getElementById("scoreBox").textContent =
      "No live metrics found yet. Run the backend scripts first.";
  }
}

// Keep the frontend synced to the backend on a fixed interval.
function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshDashboard, REFRESH_MS);
}

// Initial page load.
refreshDashboard();
startAutoRefresh();
 

// src/main.ts
import './style.css'; // Import CSS (includes Tailwind and MapLibre)
import maplibregl, { Map, Marker, Popup, LngLatBoundsLike } from 'maplibre-gl'; // Import MapLibre GL JS
import debounce from 'lodash.debounce';

// --- Configuration ---
const WS_URL = import.meta.env.VITE_BACKEND_WS_URL || "";
const API_URL = import.meta.env.VITE_BACKEND_API_URL || "";
const MAP_STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || "";

// --- DOM Elements ---
const mapContainer = document.getElementById('map');
const statusIndicator = document.getElementById('status-indicator');

// --- Application State ---
let map = null;
let webSocket = null;
let connectionStatus = 'disconnected';
let retryTimeout = null;
let retryCount = 0;
// Store GeoJSON directly for map sources
let pathSegmentsData = { type: 'FeatureCollection', features: [] };
let realtimePointsData = { type: 'FeatureCollection', features: [] };
let telemetryPopup = null;

// --- UI Update Functions ---
function updateStatusIndicator() {
  if (!statusIndicator) return;
  let text = 'Offline';
  let bgColor = 'bg-gray-500'; // Tailwind class

  switch (connectionStatus) {
    case 'connecting': text = 'Connecting...'; bgColor = 'bg-yellow-500'; break;
    case 'connected': text = 'Online'; bgColor = 'bg-green-500'; break;
    case 'error': text = 'Error'; bgColor = 'bg-red-500'; break;
    default: // disconnected
      break;
  }
  statusIndicator.textContent = text;
  // Update background color (remove old, add new) - simplistic approach
  statusIndicator.className = ''; // Clear existing classes first
  statusIndicator.classList.add(...`fixed bottom-4 right-4 px-3 py-1 rounded-full text-white text-sm shadow z-50 ${bgColor}`.split(' '));
}

// --- Map Functions ---
function initializeMap() {
  if (!mapContainer || !MAP_STYLE_URL) {
    console.error("Map container or style URL missing!");
    statusIndicator.textContent = "Map init error";
    statusIndicator.className = "bg-red-500"; // Add base classes if needed
    return;
  }

  map = new maplibregl.Map({
    container: mapContainer,
    style: MAP_STYLE_URL,
    center: [-83.74, 42.28], // Initial center (Ann Arbor)
    zoom: 10,
    attributionControl: false, // Optional: Hide default attribution if using custom
  });
  map.addControl(new maplibregl.GlobeControl(), "top-right");
  map.addControl(
    new maplibregl.NavigationControl({
      visualizePitch: true,
      visualizeRoll: true,
      showCompass: true,
      showZoom: true
    }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({
    // TODO: method for the user to change this
    unit: 'metric',
  }), 'bottom-left');
  map.addControl(
      new maplibregl.AttributionControl({
          compact: true,
          customAttribution:
              "Umich-Balloons Tracking Map | <a href='https://github.com/EricAndrechek' target='_blank'>Made by Eric</a>",
      }),
      "bottom-right"
  );
  map.addControl(
      new maplibregl.GeolocateControl({
          positionOptions: {
              enableHighAccuracy: true,
          },
        trackUserLocation: true,
        showUserHeading: true,
      })
  );
  map.addControl(
      new maplibregl.TerrainControl({
          source: "terrain",
      })
  );


  map.on('load', () => {
    console.log('Map loaded.');

    // Add sources
    map?.addSource('path-segments', { type: 'geojson', data: pathSegmentsData });
    map?.addSource('realtime-points', { type: 'geojson', data: realtimePointsData });

    // Add layers
    map?.addLayer({
      id: 'path-segments-layer', type: 'line', source: 'path-segments',
      paint: { 'line-color': '#007cbf', 'line-width': 2, 'line-opacity': 0.8 }
    });
    map?.addLayer({
      id: 'realtime-points-layer', type: 'circle', source: 'realtime-points',
      paint: { 'circle-radius': 5, 'circle-color': '#e60000', 'circle-stroke-width': 1, 'circle-stroke-color': '#ffffff' }
    });

    // Request initial data once map and WebSocket are ready
    if (connectionStatus === 'connected') {
      requestInitialData();
    }

    // Add click listener for points
    map?.on('click', 'realtime-points-layer', handlePointClick);
    map?.on('mouseenter', 'realtime-points-layer', () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
    map?.on('mouseleave', 'realtime-points-layer', () => { if (map) map.getCanvas().style.cursor = ''; });
  });

  // Debounced viewport update sender
  const debouncedSendViewportUpdate = debounce(() => {
    if (!map || connectionStatus !== 'connected') return;
    const bounds = map.getBounds();
    const bbox = {
      minLon: bounds.getWest(), minLat: bounds.getSouth(),
      maxLon: bounds.getEast(), maxLat: bounds.getNorth()
    };
    console.log('Sending updateViewport with bbox:', bbox);
    sendMessage({ type: "updateViewport", payload: { bbox } });

  }, 500); // 500ms debounce

  map.on('moveend', debouncedSendViewportUpdate);
}

function updateMapSource(sourceId, data) {
  if (!map) return;
  const source = map.getSource(sourceId);
  if (source) {
    source.setData(data);
    console.debug(`Updated map source: ${sourceId}`);
  } else {
      console.warn(`Map source '${sourceId}' not found for update.`);
  }
}

function handlePointClick(e) {
     if (e.features && e.features.length > 0) {
        const feature = e.features[0];
        const { payload_id, timestamp } = feature.properties || {};
        const coordinates = feature.geometry.coordinates.slice();

        // Ensure coordinates are numbers and within bounds
        while (Math.abs(coordinates[0]) > 180) {
            coordinates[0] = coordinates[0] > 0 ? coordinates[0] - 360 : coordinates[0] + 360;
        }

        if (payload_id !== undefined && timestamp) {
             console.log(`Clicked payload ${payload_id} at ${timestamp}`);
             fetchTelemetry(payload_id, timestamp, coordinates);
             // Show temporary popup while loading
             if (map && !telemetryPopup) {
                telemetryPopup = new Popup()
                  .setLngLat(coordinates)
                  .setHTML('<i>Loading telemetry...</i>')
                  .addTo(map);
             } else if (telemetryPopup) {
                 telemetryPopup.setLngLat(coordinates).setHTML('<i>Loading telemetry...</i>');
             }
        } else {
            console.warn("Clicked point feature missing properties:", feature.properties);
        }
     }
}


async function fetchTelemetry(payloadId, timestamp, coordinates) {
    console.log(`Workspaceing telemetry for ${payloadId} at ${timestamp}`);
    // Close existing popup immediately
    if(telemetryPopup) {
        telemetryPopup.remove();
        telemetryPopup = null;
    }

    try {
        if(!API_URL) throw new Error("Backend API URL not set");
        const response = await fetch(`${API_URL}/telemetry?payloadId=${payloadId}&timestamp=${encodeURIComponent(timestamp)}`);
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }
        const telemetryData = await response.json(); // Expects TelemetryData or null

        // Display telemetry in a MapLibre Popup
        if (map) {
            let popupHTML = `<strong>Payload ${payloadId}</strong><br/>Time: ${timestamp}<hr>`;
            if (telemetryData) {
                 // Format the data nicely - adapt based on your TelemetryData model
                 popupHTML += `Altitude: ${telemetryData.altitude ?? 'N/A'} m<br/>`;
                 popupHTML += `Speed: ${telemetryData.speed ?? 'N/A'} m/s<br/>`;
                 popupHTML += `Course: ${telemetryData.course ?? 'N/A'} °<br/>`;
                 popupHTML += `Battery: ${telemetryData.battery ?? 'N/A'} V<br/>`;
                 if(telemetryData.extra && Object.keys(telemetryData.extra).length > 0) {
                     popupHTML += `Extra: <pre>${JSON.stringify(telemetryData.extra, null, 2)}</pre>`;
                 }
            } else {
                 popupHTML += "<i>Telemetry not found.</i>";
            }

            telemetryPopup = new Popup({ closeOnClick: true, maxWidth: '300px' }) // Add closeOnClick
              .setLngLat(coordinates)
              .setHTML(popupHTML)
              .addTo(map);
        }

    } catch (error) {
        console.error("Failed to fetch or display telemetry:", error);
         if (map) { // Show error in popup
            telemetryPopup = new Popup({ closeOnClick: true })
              .setLngLat(coordinates)
              .setHTML(`<strong class='text-red-600'>Error fetching telemetry:</strong><br/>${error instanceof Error ? error.message : String(error)}`)
              .addTo(map);
         }
    }
}


// --- WebSocket Functions ---
function connectWebSocket() {
  if (webSocket && webSocket.readyState === WebSocket.OPEN) {
    console.log('WebSocket already open.');
    return;
  }
   if (!WS_URL) {
    console.error("WebSocket URL is not configured. Set VITE_BACKEND_WS_URL.");
    connectionStatus = 'error';
    updateStatusIndicator();
    return;
  }

  console.log(`Attempting WebSocket connection to ${WS_URL} (Attempt: ${retryCount + 1})...`);
  connectionStatus = 'connecting';
  updateStatusIndicator();

  webSocket = new WebSocket(WS_URL);

  webSocket.onopen = () => {
    console.log('WebSocket Connected');
    connectionStatus = 'connected';
    updateStatusIndicator();
    retryCount = 0; // Reset retries
    if (retryTimeout !== null) clearTimeout(retryTimeout);
    retryTimeout = null;

    // Request initial data if map is already loaded
    if (map?.loaded()) {
      requestInitialData();
    }
  };

  webSocket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      console.debug('WebSocket Message Received:', message.type);

      switch (message.type) {
        case 'initialPathSegments':
        case 'catchUpPathSegments':
          if (message.data && message.data.type === 'FeatureCollection') {
              pathSegmentsData = message.data;
              updateMapSource('path-segments', pathSegmentsData);
          } else {
              console.warn("Received invalid path segment data:", message.data);
          }
          break;
        case 'newPosition':
          if(message.data) {
            handleNewPosition(message.data); // Update internal state and map source
          }
          break;
        // Handle other message types like telemetryResponse if needed, though we fetch via HTTP now
        case 'error':
          console.error('WebSocket Server Error:', message.error);
          break;
        default:
          console.warn('Unknown WebSocket message type:', message.type);
      }
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error);
    }
  };

  webSocket.onerror = (error) => {
    console.error('WebSocket Error:', error);
    connectionStatus = 'error';
    updateStatusIndicator();
    // onclose will handle retry scheduling
  };

  webSocket.onclose = (event) => {
    console.log(`WebSocket Closed: Code=${event.code}, Reason=${event.reason}`);
    connectionStatus = 'disconnected';
    updateStatusIndicator();
    webSocket = null;

    // Exponential Backoff Retry Logic
    if (!event.wasClean) { // Don't retry on clean close (code 1000)
      const maxRetries = 10;
      if (retryCount < maxRetries) {
        const delay = Math.min(1000 * (2 ** retryCount), 60000); // Max ~1 min
        console.log(`Scheduling WebSocket reconnect attempt ${retryCount + 1} in ${delay / 1000}s...`);
        retryTimeout = window.setTimeout(() => { // Use window.setTimeout for browser env
           retryCount++;
           connectWebSocket(); // Attempt reconnect
        }, delay);
      } else {
        console.error(`WebSocket reconnect failed after ${maxRetries} attempts.`);
        connectionStatus = 'error'; // Show permanent error after max retries
        updateStatusIndicator();
      }
    } else {
        retryCount = 0; // Reset count on clean close
    }
  };
}

function sendMessage(messageObject) {
  if (webSocket && webSocket.readyState === WebSocket.OPEN) {
    try {
      console.debug("Sending WS Message:", messageObject.type);
      webSocket.send(JSON.stringify(messageObject));
    } catch (error) {
       console.error("Failed to send WebSocket message:", error);
    }
  } else {
    console.warn('Cannot send message, WebSocket is not connected.');
  }
}

function requestInitialData() {
    if(!map) return;
    const bounds = map.getBounds();
    const bbox = {
      minLon: bounds.getWest(), minLat: bounds.getSouth(),
      maxLon: bounds.getEast(), maxLat: bounds.getNorth()
    };
    sendMessage({
      type: "getInitialData",
      payload: { bbox: bbox, history_seconds: 10800 } // 3 hours default
    });
}

// --- Data Handling ---
function handleNewPosition(posData) {
    const featureIndex = realtimePointsData.features.findIndex(
        f => f.properties?.payload_id === posData.payload_id
    );

    const newFeature = {
        type: "Feature",
        geometry: { type: "Point", coordinates: [posData.lon, posData.lat] },
        properties: {
            payload_id: posData.payload_id,
            timestamp: posData.timestamp
        }
    };

    if (featureIndex > -1) {
        realtimePointsData.features[featureIndex] = newFeature;
    } else {
        realtimePointsData.features.push(newFeature);
    }
    // Create a *new* object to trigger reactivity if needed by libraries (though setData should be fine)
    realtimePointsData = { ...realtimePointsData, features: [...realtimePointsData.features] };
    updateMapSource('realtime-points', realtimePointsData);
}


// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  console.log("DOM Loaded. Initializing Map and WebSocket...");
  updateStatusIndicator(); // Set initial status text/color
  initializeMap();
  connectWebSocket(); // Initial connection attempt
});

// Optional: Handle page visibility changes to reconnect WS if needed
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        // If disconnected, try connecting again when tab becomes visible
        if (!webSocket || webSocket.readyState === WebSocket.CLOSED) {
            console.log("Tab became visible, attempting WebSocket reconnect...");
            // Reset retry count before manual reconnect attempt? Maybe not needed if onclose handles it.
            // retryCount = 0;
            if (retryTimeout) clearTimeout(retryTimeout); // Clear pending automatic retry
            connectWebSocket();
        }
    }
});
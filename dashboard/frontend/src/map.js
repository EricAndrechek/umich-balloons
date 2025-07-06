// src/map.js

import maplibregl, {
    Map as libreMap,
    NavigationControl,
    ScaleControl,
    AttributionControl,
    GeolocateControl,
    TerrainControl,
    GlobeControl,
    Popup
} from "maplibre-gl";
import debounce from "lodash.debounce";
import geohash from "ngeohash";
import { state } from "./state.js";
import * as config from "./config.js";
import * as ui from "./ui.js";
import {
    sendMessage,
    requestDetailsIfNeeded,
    requestInitialData,
} from "./websocket.js"; // Import specifically needed function

// --- Reusable Popup Instance ---
// Create in module scope to be accessible by exported functions
export const popup = new Popup({
    closeButton: true,
    closeOnClick: true, // Close if map clicked elsewhere
    maxWidth: '350px',  // Adjust width as needed
    className: 'balloon-popup' // Add a CSS class for styling
});

// Store the payload ID associated with the currently open popup
let currentPopupPayloadId = null;

/** Updates a specific MapLibre GeoJSON source with new data */
export function updateMapSource(sourceId, data) {
    if (!state.map || !state.map.loaded()) {
        // console.warn(`Map not ready, cannot update source '${sourceId}'`);
        return;
    }
    const source = state.map.getSource(sourceId);
    if (source && source.type === "geojson") {
        source.setData(data);
        // console.debug(`Updated map source: ${sourceId}`);
    } else {
        // Suppress warning if source just hasn't been added yet during initial load
        if (state.map.isSourceLoaded(sourceId)) {
            console.warn(
                `Map source '${sourceId}' not found or not a GeoJSONSource.`
            );
        }
    }

    // output current map layers for debugging
    const layers = state.map.getStyle().layers;
    if (layers) {
        console.debug(
            "Current map layers:",
            layers.map((layer) => layer.id)
        );
    } else {
        console.warn("No layers found in current map style.");
    }
    // output current map sources for debugging
    const sources = state.map.getStyle().sources;
    if (sources) {
        console.debug("Current map sources:", Object.keys(sources));
    } else {
        console.warn("No sources found in current map style.");
    }
    // output current map features for debugging
    const features = state.map.querySourceFeatures(sourceId);
    if (features && features.length > 0) {
        console.debug(
            `Current features in source '${sourceId}':`,
            features.map((f) => f.properties)
        );
    } else {
        console.warn(`No features found in source '${sourceId}'.`);
    }

    // get mapLatestPointData
    const latestPointSource = state.map.getSource("map-latest-points");
    if (latestPointSource && latestPointSource.type === "geojson") {
        const latestPointData = latestPointSource._data;
        console.debug(
            "Current mapLatestPointData:",
            latestPointData.features.map((f) => f.properties)
        );
    } else {
        console.warn("No features found in source 'map-latest-points'.");
    }
    console.log(state.mapLatestPointData);
}

// Debounced function to send viewport updates via WebSocket
export const debouncedSendViewportUpdate = debounce(() => {
    if (
        !state.map ||
        !state.map.loaded() ||
        state.connectionStatus !== "connected"
    ) {
        // console.debug("Skipping viewport update (map not ready or not connected)");
        return;
    }

    console.debug("Sending viewport update...");
    if (!config.IS_PROD) console.time("Geohash BBoxes");
    const bounds = state.map.getBounds();
    // Dynamic precision based on zoom level (example: adjust min/max/factor as needed)
    const precision = Math.max(
        1,
        Math.min(Math.floor(state.map.getZoom() / 2.5) + 1, 6)
    );
    try {
        const geohashes = geohash.bboxes(
            bounds.getSouth(),
            bounds.getWest(),
            bounds.getNorth(),
            bounds.getEast(),
            precision
        );
        if (!config.IS_PROD) console.timeEnd("Geohash BBoxes");

        sendMessage({
            type: "updateViewport",
            payload: { geohashes: geohashes },
        });
    } catch (error) {
        console.error("Error calculating geohash bboxes:", error);
        if (!config.IS_PROD) console.timeEnd("Geohash BBoxes"); // Ensure timer ends on error
    }
}, 500); // 500ms debounce interval

/**
 * Updates the content of the shared popup instance.
 * Only updates if the popup is open and associated with the provided payloadId.
 * @param {number|string} payloadId - The payload ID the data belongs to.
 * @param {object} data - The details data (e.g., { balloon_name: '...', telemetry: {...}, error: '...' }).
 */
export function updatePopupContent(payloadId, data) {
    // Check if the popup is open and showing details for the *correct* payload
    if (!popup.isOpen() || currentPopupPayloadId !== payloadId) {
        console.debug(`Popup not open or not for payload ${payloadId}. Skipping update.`);
        return;
    }

    console.log(`Updating popup content for payload ${payloadId}`);
    let content = `<div class="p-1">`; // Add padding via class or style
    content += `<h3 class="text-lg font-semibold mb-1">Payload ${payloadId}</h3>`; // Clearer title

    if (data.error) {
        content += `<p class="text-red-600">Error: ${data.error}</p>`;
    } else if (data.name || data.telemetry) { // Check if we got *any* useful data
        if(data.name) {
             content += `<p class="mb-1"><strong>Name:</strong> ${data.name}</p>`;
        }
        if(data.telemetry && Object.keys(data.telemetry).length > 0) {
            content += '<h4 class="text-md font-semibold mt-2 mb-1">Latest Telemetry:</h4>';
            content += '<ul class="list-disc list-inside text-sm space-y-0.5">'; // Nicer formatting
            // Format telemetry nicely (adjust keys based on actual API response)
            for (const key in data.telemetry) {
                // Format key nicely (e.g., replace underscores with spaces, capitalize)
                const formattedKey = key.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
                content += `<li><strong>${formattedKey}:</strong> ${data.telemetry[key]}</li>`;
            }
            content += '</ul>';
        } else {
             content += `<p class="text-sm italic mt-2">No telemetry data available.</p>`;
        }
        if (data.timestamp) {
             // Use current date/time based on system timezone
             const localDate = new Date(data.timestamp);
             const timeString = localDate.toLocaleTimeString([], { hour: '2-digit', minute:'2-digit', second: '2-digit' });
             // Use EDT/EST abbreviation manually or with a library like moment-timezone if needed across timezones
             const dateString = localDate.toLocaleDateString();
             content += `<p class="text-xs text-gray-500 mt-2">Updated: ${dateString} ${timeString} EDT</p>`; // Adjust timezone label as needed
        }
    } else {
        content += `<p class="text-sm italic mt-2">Details not available.</p>`;
    }
    content += `</div>`;
    popup.setHTML(content);
}

/** Initializes the MapLibre map instance, adds sources, layers, and controls */
export function initializeMap() {
    console.log("Attempting map initialization...");
    if (!ui.mapContainer) {
        console.error("FATAL: Map container not found.");
        return; // Cannot proceed
    }
    if (!config.MAP_STYLE_URL) {
        console.error("FATAL: Map style URL missing.");
        return; // Cannot proceed
    }
    if (state.map) {
        console.warn("Map already initialized.");
        return;
    }

    try {
        // --- Create Map Instance ---
        // Note: We update the shared state.map variable here
        state.map = new libreMap({
            container: ui.mapContainer,
            style: config.MAP_STYLE_URL,
            center: [-83.74, 42.28], // Default center
            zoom: 10, // Default zoom
            attributionControl: false, // Use custom AttributionControl
        });

        // --- Add Controls ---
        state.map.addControl(new GlobeControl(), "top-right");
        state.map.addControl(
            new NavigationControl({
                visualizePitch: true,
                visualizeRoll: true,
                showCompass: true,
                showZoom: true,
            }),
            "top-right"
        );
        state.map.addControl(
            new ScaleControl({ unit: "metric" }),
            "bottom-left"
        );
        state.map.addControl(
            new AttributionControl({
                compact: true,
                customAttribution:
                    "Umich-Balloons | By <a href='https://github.com/EricAndrechek' target='_blank'>Eric</a>",
            }),
            "bottom-right"
        );
        state.map.addControl(
            new GeolocateControl({
                positionOptions: { enableHighAccuracy: true },
                trackUserLocation: true,
                showUserHeading: true,
            }),
            "top-right"
        );

        // --- Map Event Listeners ---
        state.map.on("load", () => {
            console.log("Map 'load' event fired.");

            // --- Add Sources ---
            // Data is pre-populated in state.mapLineData/mapLatestPointData from cache or default empty
            if (!state.map) return; // Guard against race condition if map is destroyed quickly
            state.map.addSource("map-lines", {
                type: "geojson",
                data: state.mapLineData,
            });
            state.map.addSource("map-latest-points", {
                type: "geojson",
                data: state.mapLatestPointData,
            });

            // --- Add Layers ---
            state.map.addLayer({
                id: "map-lines-layer",
                type: "line",
                source: "map-lines",
                paint: {
                    "line-width": 2.5,
                    "line-opacity": 0.8,
                    "line-color": ["get", "color"], // Use color property from features
                },
            });

            state.map.addLayer(
                {
                    id: "map-cep-circles-layer",
                    type: "circle",
                    source: "map-latest-points", // Use the same source as the main dots
                    paint: {
                        // --- Use 'map' alignment to specify radius in meters ---
                        "circle-pitch-alignment": "map",
                        "circle-radius": [
                            // Get the 'cep' property, default to 0 if missing/null
                            "coalesce",
                            ["get", "cep"],
                            0,
                        ],
                        // --- Styling ---
                        "circle-color": "#007cbf", // Example: MapLibre blue
                        "circle-opacity": 0.15, // Low opacity
                        "circle-stroke-width": 0.5, // Optional faint stroke
                        "circle-stroke-color": "#007cbf",
                        "circle-stroke-opacity": 0.3,
                    },
                }
            ); // Add this layer *before* the main points layer if possible


            state.map.addLayer({
                id: "map-latest-points-layer",
                type: "circle",
                source: "map-latest-points",
                paint: {
                    "circle-radius": 7,
                    "circle-color": ["get", "color"], // Use color property from features
                    "circle-opacity": 1.0,
                    "circle-stroke-width": 1.5,
                    "circle-stroke-color": "#ffffff", // White outline
                },
            });

            state.map.addLayer({
                id: "map-latest-points-labels",
                type: "symbol",
                source: "map-latest-points", // Use the same point source
                minzoom: 7, // Optional: Don't show labels when zoomed far out
                layout: {
                    // Get text from feature's 'callsign' property.
                    // This property is added when balloonDetailsResponse is processed.
                    "text-field": ["get", "callsign"],
                    "text-font": [
                        "Open Sans Semibold",
                        "Arial Unicode MS Bold",
                    ], // Ensure fonts are available or use defaults
                    "text-size": 11,
                    "text-variable-anchor": ["top", "bottom", "left", "right"], // Allow text to shift position slightly to avoid overlap
                    "text-radial-offset": 1.0, // Base offset distance from the anchor (adjust as needed) - was text-offset
                    "text-justify": "auto",
                    // 'text-offset': [0, -1.6], // Offset text slightly above the circle [x, y] - Use variable anchor instead?
                    "text-anchor": "bottom", // Anchor point relative to the circle's center for offset calculation
                    "text-allow-overlap": false, // Try hard to not overlap labels
                    "text-ignore-placement": false, // Respect placement rules
                    // Prioritize labels? Maybe later: 'symbol-sort-key': ['get', 'altitude']
                },
                paint: {
                    "text-color": "#222222", // Dark text
                    "text-halo-color": "rgba(255, 255, 255, 0.85)", // Semi-transparent white halo
                    "text-halo-width": 1.5,
                    "text-halo-blur": 0.5,
                },
            });

            console.log("Map sources and layers added.");

            // --- Click Listener for Latest Points ---
            state.map.on("click", "map-latest-points-layer", (e) => {
                if (!e.features || e.features.length === 0 || !state.map)
                    return;

                const feature = e.features[0];
                const props = feature.properties;
                const coordinates = feature.geometry.coordinates.slice();
                const payloadId = props.payload_id;

                if (
                    !Array.isArray(coordinates) ||
                    coordinates.length < 2 ||
                    typeof coordinates[0] !== "number" ||
                    typeof coordinates[1] !== "number"
                ) {
                    console.error(
                        "Invalid coordinates for popup:",
                        coordinates
                    );
                    return;
                }
                // Adjust longitude for map wrapping
                while (Math.abs(e.lngLat.lng - coordinates[0]) > 180) {
                    coordinates[0] +=
                        e.lngLat.lng > coordinates[0] ? 360 : -360;
                }

                // Set loading state and open popup
                currentPopupPayloadId = payloadId; // Track which payload popup is for
                // --- Check Cache First ---
                if (state.payloadDetailsCache.has(payloadId)) {
                    console.log(`Using cached details for popup: ${payloadId}`);
                    const cachedData = state.payloadDetailsCache.get(payloadId);
                    popup.setLngLat(coordinates);
                    // Immediately update with cached data
                    updatePopupContent(payloadId, cachedData); // Call update function directly
                    if (!popup.isOpen()) popup.addTo(state.map); // Add to map if not already open
                }
                // --- Fallback: Fetch if not cached (or pending) ---
                // Check pending set too - might be loading the first time.
                else if (state.pendingDetailRequests.has(payloadId)) {
                    console.log(
                        `Details pending for ${payloadId}, showing loading...`
                    );
                    popup
                        .setLngLat(coordinates)
                        .setHTML(
                            `<div class="p-1"><h3>Loading details for ${payloadId}...</h3></div>`
                        )
                        .addTo(state.map);
                } else {
                    // Not cached, not pending -> Should not happen if pre-fetch works, but request as fallback
                    console.warn(
                        `Details for ${payloadId} not cached or pending. Requesting on click...`
                    );
                    popup
                        .setLngLat(coordinates)
                        .setHTML(
                            `<div class="p-1"><h3>Loading details for ${payloadId}...</h3></div>`
                        )
                        .addTo(state.map);
                    // Trigger the fetch (again)
                    requestDetailsIfNeeded(payloadId); // Use the helper to avoid duplicates
                }
            });

            // --- Cursor Change on Hover ---
            state.map.on("mouseenter", "map-latest-points-layer", () => {
                if (state.map) state.map.getCanvas().style.cursor = "pointer";
            });
            state.map.on("mouseleave", "map-latest-points-layer", () => {
                if (state.map) state.map.getCanvas().style.cursor = "";
            });

            // Remove tracking of payload ID when popup closes
            popup.on("close", () => {
                console.log(
                    `Popup closed for payload ${currentPopupPayloadId}`
                );
                currentPopupPayloadId = null;
            });

            // --- Attach Map Interaction Listeners ---
            state.map.on("moveend", debouncedSendViewportUpdate); // Send viewport update after map movement stops
            // Add other listeners like 'click' on layers if needed here

            // --- Initial Data Request ---
            // If WebSocket is already connected when map loads, request initial data
            if (state.connectionStatus === "connected") {
                console.log(
                    "Map loaded and WebSocket connected, requesting initial data..."
                );
                requestInitialData(); // Fetch data for the current view
            } else {
                console.log(
                    "Map loaded, waiting for WebSocket connection to request initial data."
                );
            }
        });

        state.map.on("error", (e) => {
            console.error("MapLibre Error:", e);
            // Optional: Update status indicator to show map error
            // state.connectionStatus = 'error'; // Or a specific map error status?
            // ui.updateStatusIndicator();
        });
    } catch (error) {
        console.error("FATAL ERROR initializing MapLibre Map:", error);
        state.connectionStatus = "error"; // Set state to error
        ui.updateStatusIndicator(); // Update UI to reflect error
        state.map = null; // Ensure map state is null on failure
    }
}

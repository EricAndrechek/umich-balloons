// src/map.js

import maplibregl, {
    Map as libreMap,
    NavigationControl,
    ScaleControl,
    AttributionControl,
    GeolocateControl,
    TerrainControl,
    GlobeControl,
} from "maplibre-gl";
import debounce from "lodash.debounce";
import geohash from "ngeohash";
import * as state from "./state.js";
import * as config from "./config.js";
import * as ui from "./ui.js";
import { sendMessage } from "./websocket.js"; // Import specifically needed function
import { requestInitialData } from "./websocket.js"; // Import specifically needed function

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
                    "Umich-Balloons | <a href='https://github.com/EricAndrechek' target='_blank'>Eric Andrechek</a>",
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
        // Add Terrain control conditionally if source exists in style
        // state.map.addControl(new TerrainControl({ source: "terrain" })); // Uncomment if 'terrain' source is defined

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
            state.map.addLayer({
                id: "map-latest-points-layer",
                type: "circle",
                source: "map-latest-points",
                paint: {
                    "circle-radius": 6,
                    "circle-color": ["get", "color"], // Use color property from features
                    "circle-opacity": 1.0,
                    "circle-stroke-width": 1.5,
                    "circle-stroke-color": "#ffffff", // White outline
                },
            });

            console.log("Map sources and layers added.");

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

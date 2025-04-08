// src/main.js

import "./style.css"; // Import CSS (includes Tailwind and MapLibre)
// Import only runtime values needed from maplibre-gl
import maplibregl, {
    Map as libreMap,
    Marker,
    Popup,
    NavigationControl,
    ScaleControl,
    AttributionControl,
    GeolocateControl,
} from "maplibre-gl";
import debounce from "lodash.debounce";
import * as h3 from "h3-js";
import * as turf from "@turf/turf";
import geohash from "ngeohash"; // Geohash library

// --- Configuration ---
// if url starts with "https://", assume prod
export const IS_PROD = location.href.startsWith("https://") ? true : import.meta.env.VITE_DEV !== "true";
// import based on environment
const WS_URL = IS_PROD
? import.meta.env.VITE_WS_URL_PROD
: import.meta.env.VITE_WS_URL_DEV;
const API_URL = IS_PROD
? import.meta.env.VITE_API_URL_PROD
: import.meta.env.VITE_API_URL_DEV;
console.debug(
    `Using ${IS_PROD ? "production" : "development"} configuration. WS_URL: ${WS_URL}, API_URL: ${API_URL}`
)

const MAP_STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || "";

// --- DOM Elements ---
const mapContainer = document.getElementById("map");
const statusIndicator = document.getElementById("status-indicator");

// --- Application State ---
/** @type {Map | null} */
let map = null;
/** @type {WebSocket | null} */
let webSocket = null;
/** @type {'connecting' | 'connected' | 'disconnected' | 'error'} */
let connectionStatus = "disconnected";
/** @type {number | null} */
let retryTimeout = null;
let retryCount = 0;

// --- Map Data State ---
// Holds LineString features received from backend + dynamically added segments
let mapLineData = { type: "FeatureCollection", features: [] };
// Holds ONLY the latest Point feature for each payload
let mapLatestPointData = { type: "FeatureCollection", features: [] };
// Keep track of the latest known coordinate *per payload* for extending lines
const latestCoords = new Map(); // Map<payload_id, [lon, lat]>

// --- Color Assignment ---
function getColorForPayload(payloadId) {
    // given a payload ID, return a unique color
    let hash = 0;
    for (let i = 0; i < payloadId.length; i++) {
        hash = payloadId.charCodeAt(i) + ((hash << 5) - hash);
    }
    let color = "#";
    color += ((hash >> 24) & 0xff).toString(16).padStart(2, "0");
    color += ((hash >> 16) & 0xff).toString(16).padStart(2, "0");
    color += ((hash >> 8) & 0xff).toString(16).padStart(2, "0");
    return color;
};

/** Updates the text and color of the status indicator */
function updateStatusIndicator() {
    if (!statusIndicator) return;
    let text = "Offline";
    let bgColor = "bg-gray-500";
    
    switch (connectionStatus) {
        case "connecting":
        text = "Connecting...";
        bgColor = "bg-yellow-500";
        break;
        case "connected":
        text = "Online";
        bgColor = "bg-green-500";
        break;
        case "error":
        text = "Error";
        bgColor = "bg-red-500";
        break;
        default:
        break; // disconnected
    }
    statusIndicator.textContent = text;
    statusIndicator.className = "";
    statusIndicator.classList.add(
        ...`fixed bottom-4 right-4 px-3 py-1 rounded-full text-white text-sm shadow z-50 ${bgColor}`.split(
            " "
        )
    );
}

/** Updates a MapLibre GeoJSON source with new data */
function updateMapSource(sourceId, data) {
    if (!map || !map.loaded()) return;
    const source = map.getSource(sourceId);
    if (source && source.type === "geojson") {
        source.setData(data);
    } else {
        console.warn(
            `Map source '${sourceId}' not found or not a GeoJSONSource.`
        );
    }
}

// --- Data Processing Functions ---

/** Processes incoming LineString segments (initial or catchup) */
function processPathSegments(segmentCollection) {
    console.log(
        `Processing ${segmentCollection.features.length} path segments...`
    );
    let needsLineUpdate = false;
    
    segmentCollection.features.forEach((segmentFeature) => {
        const props = segmentFeature.properties;
        const geometry = segmentFeature.geometry;
        
        // Basic validation
        if (
            !props ||
            typeof props.payload_id !== "number" ||
            geometry?.type !== "LineString" ||
            !geometry.coordinates ||
            geometry.coordinates.length < 2
        ) {
            console.warn("Skipping invalid segment feature:", segmentFeature);
            return;
        }
        
        const payloadId = props.payload_id;
        const color = getColorForPayload(payloadId);
        // Use a consistent feature ID format
        const featureId =
        segmentFeature.id ??
        `segment-${payloadId}-${geometry.coordinates[0][0].toFixed(5)}`;
        
        const featureProperties = { payload_id: payloadId, color: color };
        
        // Find if a feature with this payload ID already exists *to merge or replace*
        // Simple approach: Replace all segments for a payload when new segments arrive for it.
        // More complex: Merge segments if they are contiguous in time (needs timestamp info).
        // Let's stick to replacing for now, assuming MV provides recent complete-ish paths.
        const existingFeatureIndex = mapLineData.features.findIndex(
            (f) => f.properties?.payload_id === payloadId && f.id === featureId
        ); // Check ID match too
        
        const newFeature = {
            type: "Feature",
            id: featureId,
            properties: featureProperties,
            geometry: geometry, // Use the geometry directly
        };
        
        if (existingFeatureIndex > -1) {
            mapLineData.features[existingFeatureIndex] = newFeature;
        } else {
            mapLineData.features.push(newFeature);
        }
        needsLineUpdate = true;
        
        // --- Record the LAST point of this segment ---
        const lastCoord = geometry.coordinates[geometry.coordinates.length - 1];
        if (
            lastCoord &&
            typeof lastCoord[0] === "number" &&
            typeof lastCoord[1] === "number"
        ) {
            console.debug(
                `Updating latestCoord for ${payloadId} from segment ${featureId} to:`,
                lastCoord
            );
            latestCoords.set(payloadId, lastCoord); // Store [lon, lat]
        } else {
            console.warn(
                `Segment ${featureId} for payload ${payloadId} has invalid last coordinate.`
            );
        }
    });
    
    if (needsLineUpdate) {
        mapLineData = { ...mapLineData, features: [...mapLineData.features] }; // New object reference
        updateMapSource("map-lines", mapLineData);
    }
}

/** Handles a single real-time point update */
function handleNewPosition(posData) {
    const payloadId = posData.payload_id;
    const newCoord = [posData.lon, posData.lat];
    const newTimestamp = posData.timestamp;
    const color = getColorForPayload(payloadId);
    let needsLineUpdate = false;
    let needsPointUpdate = false;
    
    // --- Append coordinate to the appropriate LineString Feature ---
    const lastCoord = latestCoords.get(payloadId);
    
    // --- Find or Create LineString Feature ---
    // We need *one* LineString per payload to append to.
    // Let's use a consistent ID like `line-${payloadId}`
    const lineFeatureId = `line-${payloadId}`;
    let lineFeature = mapLineData.features.find((f) => f.id === lineFeatureId);
    
    if (lineFeature && lineFeature.geometry.type === "LineString") {
        // Feature exists, append coordinate
        // Avoid duplicates based on coordinate only (simplistic check)
        const lastInLine =
        lineFeature.geometry.coordinates[
            lineFeature.geometry.coordinates.length - 1
        ];
        if (
            !lastInLine ||
            lastInLine[0] !== newCoord[0] ||
            lastInLine[1] !== newCoord[1]
        ) {
            lineFeature.geometry.coordinates.push(newCoord);
            needsLineUpdate = true;
            console.debug(`Appended coord to existing line ${lineFeatureId}`);
        } else {
            console.debug(`Skipping duplicate coord add for ${payloadId}`);
        }
    } else if (lastCoord) {
        // Feature doesn't exist, but we have a previous point -> Create line segment
        console.debug(`Creating new line ${lineFeatureId} from lastCoord`);
        lineFeature = {
            type: "Feature",
            id: lineFeatureId,
            properties: { payload_id: payloadId, color: color },
            geometry: {
                type: "LineString",
                coordinates: [lastCoord, newCoord],
            }, // Start with two points
        };
        mapLineData.features.push(lineFeature); // Add to the collection
        needsLineUpdate = true;
    } else {
        // First point ever seen for this payload, cannot draw a line yet
        console.debug(`First point for ${payloadId}, cannot draw line yet.`);
        // Store it so the *next* point can connect to it
    }
    
    // --- Update latestCoords Map (always update) ---
    latestCoords.set(payloadId, newCoord);
    
    // --- Update Latest Point Marker Source ---
    const latestPointFeatureId = `latest-${payloadId}`;
    const latestPointFeatureIndex = mapLatestPointData.features.findIndex(
        (f) => f.id === latestPointFeatureId
    );
    const newLatestPointFeature = {
        type: "Feature",
        id: latestPointFeatureId,
        properties: {
            payload_id: payloadId,
            timestamp: newTimestamp,
            color: color,
        },
        geometry: { type: "Point", coordinates: newCoord },
    };
    
    if (latestPointFeatureIndex > -1) {
        mapLatestPointData.features[latestPointFeatureIndex] =
        newLatestPointFeature;
    } else {
        mapLatestPointData.features.push(newLatestPointFeature);
    }
    needsPointUpdate = true; // Flag that points need update
    
    // --- Trigger Map Updates ---
    // Update sources only if needed, creating new object references
    if (needsLineUpdate) {
        mapLineData = { ...mapLineData, features: [...mapLineData.features] };
        updateMapSource("map-lines", mapLineData);
    }
    if (needsPointUpdate) {
        mapLatestPointData = {
            ...mapLatestPointData,
            features: [...mapLatestPointData.features],
        };
        updateMapSource("map-latest-points", mapLatestPointData);
    }
}

// --- Map Initialization ---
function initializeMap() {
    console.log("Attempting map initialization...");
    if (!mapContainer) {
        console.error("FATAL: Map container not found.");
        return;
    }
    if (!MAP_STYLE_URL) {
        console.error("FATAL: Map style URL missing.");
        return;
    }
    if (map) {
        console.warn("Map already initialized.");
        return;
    }
    
    try {
        map = new libreMap({
            container: mapContainer,
            style: MAP_STYLE_URL,
            center: [-83.74, 42.28],
            zoom: 10,
            attributionControl: false,
        });
        
        map.addControl(new maplibregl.GlobeControl(), "top-right");
        
        map.addControl(
            new maplibregl.NavigationControl({
                visualizePitch: true,
                
                visualizeRoll: true,
                
                showCompass: true,
                
                showZoom: true,
            }),
            "top-right"
        );
        
        map.addControl(
            new maplibregl.ScaleControl({
                // TODO: method for the user to change this
                
                unit: "metric",
            }),
            "bottom-left"
        );
        
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
        
        map.on("load", () => {
            console.log("Map loaded.");
            
            // Add Sources (start empty)
            map?.addSource("map-lines", { type: "geojson", data: mapLineData });
            map?.addSource("map-latest-points", {
                type: "geojson",
                data: mapLatestPointData,
            });
            
            // Add Layers
            map?.addLayer({
                id: "map-lines-layer",
                type: "line",
                source: "map-lines",
                paint: {
                    "line-width": 2.5,
                    "line-opacity": 0.75,
                    "line-color": ["get", "color"],
                },
            });
            map?.addLayer({
                id: "map-latest-points-layer",
                type: "circle",
                source: "map-latest-points",
                paint: {
                    "circle-radius": 6,
                    "circle-color": ["get", "color"],
                    "circle-opacity": 1.0,
                    "circle-stroke-width": 1.5,
                    "circle-stroke-color": "#ffffff",
                },
            });
            
            // Event Listeners
            map?.on("moveend", debouncedSendViewportUpdate);
            
            // Initial Data Request
            if (connectionStatus === "connected") requestInitialData();
        });
        
        map.on("error", (e) => {
            console.error("MapLibre Error:", e); /* Update status? */
        });
    } catch (error) {
        console.error("FATAL ERROR initializing MapLibre Map:", error);
        if (statusIndicator) {
            /* Show error in status */
        }
    }
}

// Debounced viewport update sender
const debouncedSendViewportUpdate = debounce(() => {

    // if (!map || connectionStatus !== "connected") return;
    if (!map) return;

    if (!IS_PROD) console.time("Geohash");
    const bounds = map.getBounds();
    const precision = Math.max(Math.min(Math.floor(map.getZoom() * 0.5) - 1, 4), 1);
    const geohashes = geohash.bboxes(
        bounds.getSouth(),
        bounds.getWest(),
        bounds.getNorth(),
        bounds.getEast(),
        precision
    );
    if (!IS_PROD) console.timeEnd("Geohash");
    
    sendMessage({ type: "updateViewport", payload: { geohashes: geohashes} });
}, 500);

// --- WebSocket Functions ---
function connectWebSocket() {
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
        console.log("WebSocket already open.");
        return;
    }
    if (!WS_URL) {
        console.error(
            "WebSocket URL is not configured. Set VITE_BACKEND_WS_URL."
        );
        connectionStatus = "error";
        updateStatusIndicator();
        return;
    }
    
    console.log(
        `Attempting WebSocket connection to ${WS_URL} (Attempt: ${
            retryCount + 1
        })...`
    );
    connectionStatus = "connecting";
    updateStatusIndicator();
    webSocket = new WebSocket(WS_URL);
    
    webSocket.onopen = () => {
        console.log("WebSocket Connected");
        connectionStatus = "connected";
        updateStatusIndicator();
        retryCount = 0;
        if (retryTimeout !== null) clearTimeout(retryTimeout);
        retryTimeout = null;
        if (map?.loaded()) requestInitialData(); // Request data if map ready
    };
    
    webSocket.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            const msgType = message.type;
            switch (msgType) {
                case "initialPathSegments":
                case "catchUpPathSegments":
                console.log(`Handling ${msgType}...`);
                if (message.data?.type === "FeatureCollection") {
                    processPathSegments(message.data);
                } else {
                    console.warn(`Invalid ${msgType} data:`, message.data);
                }
                break;
                case "newPosition":
                if (message.data) handleNewPosition(message.data);
                break;
                case "error":
                console.error("WebSocket Server Error:", message.error);
                break;
                default:
                console.warn("Unknown WebSocket message type:", msgType);
            }
        } catch (error) {
            console.error("Failed to parse/handle WebSocket message:", error);
        }
    };
    
    webSocket.onerror = (error) => {
        console.error("WebSocket Error:", error);
        connectionStatus = "error";
        updateStatusIndicator();
        // onclose will handle retry scheduling
    };
    
    webSocket.onclose = (event) => {
        console.log(
            `WebSocket Closed: Code=${event.code}, Reason=${event.reason}`
        );
        connectionStatus = "disconnected";
        updateStatusIndicator();
        webSocket = null;
        
        // Exponential Backoff Retry Logic
        if (!event.wasClean) {
            // Don't retry on clean close (code 1000)
            const maxRetries = 10;
            if (retryCount < maxRetries) {
                const delay = Math.min(1000 * 2 ** retryCount, 60000); // Max ~1 min
                console.log(
                    `Scheduling WebSocket reconnect attempt ${
                        retryCount + 1
                    } in ${delay / 1000}s...`
                );
                retryTimeout = window.setTimeout(() => {
                    // Use window.setTimeout for browser env
                    retryCount++;
                    connectWebSocket(); // Attempt reconnect
                }, delay);
            } else {
                console.error(
                    `WebSocket reconnect failed after ${maxRetries} attempts.`
                );
                connectionStatus = "error"; // Show permanent error after max retries
                updateStatusIndicator();
            }
        } else {
            retryCount = 0; // Reset count on clean close
        }
    };
}
// Function to send messages
function sendMessage(messageObject) {
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
        try {
            // console.debug("Sending WS Message:", messageObject.type); // Less verbose
            webSocket.send(JSON.stringify(messageObject));
        } catch (error) {
            console.error("Failed to send WebSocket message:", error);
        }
    } else {
        console.warn("Cannot send message, WebSocket is not connected.");
    }
}

// Function to request initial data
function requestInitialData() {
    if (!map || !map.loaded() || connectionStatus !== "connected") {
        console.log("Map not ready for initial data request or not connected.");
        return;
    }

    if (!IS_PROD) console.time("Geohash");
    const bounds = map.getBounds();
    const precision = Math.max(
        Math.min(Math.floor(map.getZoom() * 0.5) - 1, 4),
        1
    );
    const geohashes = geohash.bboxes(
        bounds.getSouth(),
        bounds.getWest(),
        bounds.getNorth(),
        bounds.getEast(),
        precision
    );
    if (!IS_PROD) console.timeEnd("Geohash");

    sendMessage({
        type: "getInitialData",
        payload: { geohashes: geohashes, history_seconds: 10800 },
    }); // 3 hours default
}

// --- Event Listeners Setup (Only if Geolocation buttons exist) ---
function setupEventListeners() {}

// --- Initialization ---
document.addEventListener("DOMContentLoaded", () => {
    console.log("DOM Loaded. Initializing Map and WebSocket...");
    updateStatusIndicator();
    initializeMap(); // Initialize map AFTER DOM is loaded
    connectWebSocket(); // Initial connection attempt
    setupEventListeners(); // Setup button listeners if they exist
});

// --- Visibility Change Listener ---
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        if (!webSocket || webSocket.readyState === WebSocket.CLOSED) {
            console.log(
                "Tab became visible, attempting WebSocket reconnect..."
            );
            if (retryTimeout) clearTimeout(retryTimeout);
            // Resetting count might cause rapid retries if server is still down
            // retryCount = 0;
            connectWebSocket();
        }
    }
});

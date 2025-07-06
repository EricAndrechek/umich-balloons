// src/main.js

import "./style.css"; // Ensure styles are loaded
import * as config from "./config.js"; // Load config early (logs URLs)
import { state } from "./state.js";
import * as ui from "./ui.js"; // UI elements and functions
import * as cache from "./cache.js"; // Caching functions
import { initializeMap, debouncedSendViewportUpdate } from "./map.js"; // Map functions
import { connectWebSocket } from "./websocket.js"; // WebSocket functions

// --- Initialization Sequence ---
document.addEventListener("DOMContentLoaded", () => {
    console.log("DOM Loaded. Starting application initialization...");

    // 1. Check for essential DOM elements
    if (!ui.mapContainer || !ui.statusIndicator) {
        console.error(
            "FATAL: Required DOM elements (#map or #status-indicator) not found. Aborting."
        );
        // Display error to user?
        document.body.innerHTML =
            '<p style="color: red; padding: 20px;">Error: Application could not start. Required elements missing.</p>';
        return;
    }

    // 2. Try loading data from cache
    const loadedFromCache = cache.loadDataFromCache();
    if (loadedFromCache) {
        console.log("Initialized state with data from cache.");
    } else {
        console.log("No valid cache data loaded, starting with empty state.");
        // Ensure state is clean (although default state should be empty)
        state.mapLineData = { type: "FeatureCollection", features: [] };
        state.mapLatestPointData = { type: "FeatureCollection", features: [] };
        state.latestCoords.clear();
    }

    // 3. Update status indicator (will show initial 'disconnected' state)
    ui.updateStatusIndicator();

    // 4. Initialize the map (uses data loaded from cache, if any)
    initializeMap(); // This function handles its own errors internally

    // 5. Attempt initial WebSocket connection (only if map init didn't fail critically)
    if (state.connectionStatus !== "error") {
        // Reset retry count before the first connection attempt sequence
        state.retryCount = 0;
        if (state.retryTimeout) clearTimeout(state.retryTimeout);
        state.retryTimeout = null;
        connectWebSocket();
    } else {
        console.warn(
            "Skipping initial WebSocket connection due to prior initialization error."
        );
    }

    // 6. Setup other UI event listeners (if any)
    ui.setupEventListeners();

    console.log("Initialization sequence complete.");
});

// --- Visibility Change Listener ---
// Handles reconnecting or updating view when tab becomes visible again
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        console.log("Tab became visible.");
        // If WS is closed AND we are not already trying to connect AND not in a permanent error state: try connecting.
        if (
            (!state.webSocket ||
                state.webSocket.readyState === WebSocket.CLOSED) &&
            state.connectionStatus !== "connecting" &&
            state.connectionStatus !== "error"
        ) {
            console.log(
                "WebSocket seems closed. Attempting reconnect on visibility gain..."
            );
            // Optionally reset retry count here if desired behavior is to always retry freshly when tab is focused
            // state.retryCount = 0;
            if (state.retryTimeout) clearTimeout(state.retryTimeout); // Clear existing timer
            state.retryTimeout = null;
            connectWebSocket(); // Trigger connection attempt
        }
        // If map is loaded and WS is connected, send a viewport update
        else if (
            state.map?.loaded() &&
            state.connectionStatus === "connected"
        ) {
            console.log("Tab visible and connected: Sending viewport update.");
            // Use the debounced function, or call sendMessage directly if immediate update is desired
            debouncedSendViewportUpdate(); // Trigger map viewport update
            // debouncedSendViewportUpdate.flush(); // If we want to force immediate execution (requires lodash debounce setup)
        }
    } else {
        console.log("Tab became hidden.");
        // Consider disconnecting WebSocket cleanly when tab is hidden
        // if (state.webSocket && state.webSocket.readyState === WebSocket.OPEN) {
        //     console.log("Closing WebSocket connection while tab is hidden.");
        //     state.webSocket.close(1000, "Tab hidden"); // 1000 = Normal Closure
        // }
    }
});

// --- Error Handling ---
// Global error handler (optional, but good practice)
window.addEventListener("error", (event) => {
    console.error(
        "Unhandled global error:",
        event.message,
        event.filename,
        event.lineno,
        event.colno,
        event.error
    );
    // could potentially update the UI status here as well
    // state.connectionStatus = 'error';
    // ui.updateStatusIndicator();
});

window.addEventListener("unhandledrejection", (event) => {
    console.error("Unhandled promise rejection:", event.reason);
    // state.connectionStatus = 'error';
    // ui.updateStatusIndicator();
});

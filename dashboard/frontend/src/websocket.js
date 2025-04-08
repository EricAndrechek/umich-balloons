// src/websocket.js

import { state } from "./state.js";
import * as config from "./config.js";
import * as ui from "./ui.js";
import * as cache from "./cache.js";
import * as dataProcessor from "./dataProcessor.js";
import { updateMapSource, updatePopupContent } from "./map.js";
import geohash from "ngeohash"; // For initial data request

/** Send a message object over the WebSocket connection */
export function sendMessage(messageObject) {
    if (state.webSocket && state.webSocket.readyState === WebSocket.OPEN) {
        try {
            // console.debug("Sending WS Message:", messageObject.type, messageObject.payload); // Verbose
            state.webSocket.send(JSON.stringify(messageObject));
        } catch (error) {
            console.error("Failed to send WebSocket message:", error);
        }
    } else {
        console.warn(
            "Cannot send message, WebSocket is not connected or not open yet."
        );
    }
}

/** Request initial path/point data for the current map view */
export function requestInitialData() {
    if (
        !state.map ||
        !state.map.loaded() ||
        state.connectionStatus !== "connected"
    ) {
        console.log(
            "Cannot send initial data request: Map not ready or not connected."
        );
        return;
    }

    console.log("Requesting initial data for current map bounds...");
    if (!config.IS_PROD) console.time("Geohash BBoxes (Initial)");
    const bounds = state.map.getBounds();
    const precision = Math.max(
        1,
        Math.min(Math.floor(state.map.getZoom() / 2.5) + 1, 6)
    ); // Use same precision as viewport updates
    try {
        const geohashes = geohash.bboxes(
            bounds.getSouth(),
            bounds.getWest(),
            bounds.getNorth(),
            bounds.getEast(),
            precision
        );
        if (!config.IS_PROD) console.timeEnd("Geohash BBoxes (Initial)");

        sendMessage({
            type: "getInitialData",
            payload: {
                geohashes: geohashes,
                // Request history roughly matching cache duration to fill gaps
                history_seconds: config.CACHE_DURATION_MS / 1000,
            },
        });
    } catch (error) {
        console.error(
            "Error calculating geohash bboxes for initial request:",
            error
        );
        if (!config.IS_PROD) console.timeEnd("Geohash BBoxes (Initial)");
    }
}

export function requestDetailsIfNeeded(payloadId) {
    if (
        !state.payloadDetailsCache.has(payloadId) &&
        !state.pendingDetailRequests.has(payloadId)
    ) {
        console.log(`Details for ${payloadId} not cached. Requesting...`);
        state.pendingDetailRequests.add(payloadId); // Mark as pending
        sendMessage({
            type: "getBalloonDetails",
            payload: { payload_id: payloadId },
        });
    }
    // else { console.debug(`Details for ${payloadId} already cached or pending.`); }
}

/** Establish WebSocket connection with retry logic */
export function connectWebSocket() {
    if (state.webSocket && state.webSocket.readyState === WebSocket.OPEN) {
        console.log("WebSocket already open.");
        return;
    }
    if (
        state.webSocket &&
        state.webSocket.readyState === WebSocket.CONNECTING
    ) {
        console.log("WebSocket connection attempt already in progress.");
        return;
    }
    if (!config.WS_URL) {
        console.error("WebSocket URL is not configured. Cannot connect.");
        state.connectionStatus = "error"; // Permanent error if no URL
        ui.updateStatusIndicator();
        return;
    }

    // Clear any existing retry timeout
    if (state.retryTimeout !== null) {
        clearTimeout(state.retryTimeout);
        state.retryTimeout = null;
    }

    console.log(
        `Attempting WebSocket connection to ${config.WS_URL} (Attempt: ${
            state.retryCount + 1
        })...`
    );
    state.connectionStatus = "connecting";
    ui.updateStatusIndicator();

    // Update state directly
    state.webSocket = new WebSocket(config.WS_URL);

    state.webSocket.onopen = () => {
        console.log("WebSocket Connected");
        state.connectionStatus = "connected";
        ui.updateStatusIndicator();
        state.retryCount = 0; // Reset retries on successful connection

        // If map is ready, request initial data for the current view
        if (state.map?.loaded()) {
            requestInitialData();
        } else {
            console.log(
                "WebSocket opened, waiting for map 'load' event to request initial data."
            );
        }
    };

    state.webSocket.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            const msgType = message.type;
            let updateResult = {
                needsLineUpdate: false,
                needsPointUpdate: false,
            };
            let segmentsProcessed = false;

            // console.debug("Received WS Message:", msgType); // Verbose

            switch (msgType) {
                case "initialPathSegments":
                case "catchUpPathSegments":
                    // --- MODIFICATION START ---
                    // Check if data is the array structure received from backend
                    if (Array.isArray(message.data)) {
                        const receivedSegments = message.data;
                        console.log(
                            `Processing ${receivedSegments.length} segments from ${msgType}...`
                        );

                        // Create a valid FeatureCollection to pass downstream
                        const featureCollection = {
                            type: "FeatureCollection",
                            features: [],
                        };

                        receivedSegments.forEach((segmentData) => {
                            try {
                                // --- Data Validation ---
                                if (segmentData?.payload_id == null) {
                                    console.warn(
                                        `Skipping segment in ${msgType}: Missing payload_id`,
                                        segmentData
                                    );
                                    return; // Continue to next item in forEach
                                }
                                if (
                                    typeof segmentData.path_segment_geojson !==
                                    "string"
                                ) {
                                    console.warn(
                                        `Skipping segment in ${msgType} for ${segmentData.payload_id}: path_segment_geojson is not a string`,
                                        segmentData
                                    );
                                    return;
                                }

                                // --- Parse the nested GeoJSON string ---
                                const geometry = JSON.parse(
                                    segmentData.path_segment_geojson
                                );

                                // --- Geometry Validation ---
                                if (
                                    geometry?.type !== "LineString" ||
                                    !Array.isArray(geometry.coordinates) ||
                                    geometry.coordinates.length < 2
                                ) {
                                    console.warn(
                                        `Parsed GeoJSON is not a valid LineString in ${msgType} for payload ${segmentData.payload_id}:`,
                                        geometry
                                    );
                                    return; // Skip this segment
                                }

                                const payloadId = segmentData.payload_id;

                                // --- Trigger Detail Request ---
                                // (This part remains the same logic)
                                requestDetailsIfNeeded(payloadId);

                                // --- Construct GeoJSON Feature ---
                                const feature = {
                                    type: "Feature",
                                    // Note: dataProcessor.processPathSegments will generate its own consistent ID
                                    properties: {
                                        payload_id: payloadId,
                                        // Copy other potentially useful properties from the segment data
                                        time_bin_start:
                                            segmentData.time_bin_start,
                                        first_point_time:
                                            segmentData.first_point_time,
                                        last_point_time:
                                            segmentData.last_point_time,
                                        // 'color' will be added by dataProcessor
                                    },
                                    geometry: geometry, // Use the parsed geometry object
                                };
                                featureCollection.features.push(feature);
                            } catch (parseError) {
                                console.error(
                                    `Error parsing path_segment_geojson for payload ${segmentData?.payload_id} in ${msgType}:`,
                                    parseError,
                                    "\nString was:",
                                    segmentData.path_segment_geojson
                                );
                            }
                        }); // End forEach

                        // --- Process the constructed FeatureCollection ---
                        if (featureCollection.features.length > 0) {
                            console.log(
                                `Passing ${featureCollection.features.length} constructed features to processPathSegments.`
                            );
                            // Call the processor with the correctly formatted FeatureCollection
                            segmentsProcessed =
                                dataProcessor.processPathSegments(
                                    featureCollection
                                );
                            updateResult.needsLineUpdate = segmentsProcessed;
                        } else {
                            console.log(
                                `No valid features constructed from ${msgType} data.`
                            );
                        }
                    } else {
                        // If it's not the array structure, log the warning as before
                        console.warn(
                            `Invalid ${msgType} data structure (expected array of segments, got ${typeof message.data}):`,
                            message.data
                        );
                    }
                    // --- MODIFICATION END ---
                    break;
                case "newPosition":
                    if (message.data?.payload_id != null) {
                        // Trigger detail request if this ID is new
                        requestDetailsIfNeeded(message.data.payload_id);
                        updateResult = dataProcessor.handleNewPosition(
                            message.data
                        );
                    } else {
                        console.warn("Invalid newPosition data:", message.data);
                    }
                    break;
                // --- Handle Balloon Details Response ---
                case "balloonDetailsResponse":
                    if (message.data && message.payload_id != null) {
                        const payloadId = message.payload_id;
                        console.debug(
                            `Received balloon details for ${payloadId}:`,
                            message.data
                        );

                        // --- Cache the data ---
                        state.payloadDetailsCache.set(payloadId, message.data);
                        state.pendingDetailRequests.delete(payloadId); // Remove from pending

                        // --- Update the corresponding point feature for label ---
                        const pointFeatureIndex =
                            state.mapLatestPointData.features.findIndex(
                                (f) => f.properties?.payload_id === payloadId
                            );
                        if (pointFeatureIndex > -1) {
                            const feature =
                                state.mapLatestPointData.features[
                                    pointFeatureIndex
                                ];
                            // Add/update the property used for the label (e.g., 'balloon_name')
                            // Make sure 'balloon_name' exists in your response data
                            feature.properties.callsign =
                                message.data.balloon_name || `ID: ${payloadId}`; // Fallback label
                            // Optionally add all details if useful for popup directly
                            // feature.properties.details = message.data;

                            // Ensure a new object reference for reactivity if needed, though modifying properties often works
                            state.mapLatestPointData.features[
                                pointFeatureIndex
                            ] = {
                                ...feature,
                                properties: { ...feature.properties },
                            };
                            state.mapLatestPointData = {
                                ...state.mapLatestPointData,
                                features: [
                                    ...state.mapLatestPointData.features,
                                ],
                            };

                            needsPointSourceUpdate = true; // Signal that the point source needs updating for the label
                            console.log(
                                `Updated feature ${feature.id} with callsign: ${feature.properties.callsign}`
                            );
                        } else {
                            console.warn(
                                `Received details for ${payloadId}, but no matching point feature found.`
                            );
                        }
                        // Call the imported function to update the popup content
                        updatePopupContent(message.payload_id, message.data);
                    } else {
                        console.warn(
                            "Invalid balloonDetailsResponse received:",
                            message
                        );
                    }
                    break;
                // --- End Handle Details ---
                case "error":
                    console.error(
                        "WebSocket Server Error Message:",
                        message.error
                    );
                    // Decide if this server error requires action (e.g., disconnect, UI update)
                    break;
                default:
                    console.warn("Unknown WebSocket message type:", msgType);
            }

            // --- Post-processing: Update map sources and cache if necessary ---
            if (updateResult.needsLineUpdate) {
                updateMapSource("map-lines", state.mapLineData);
            }
            if (updateResult.needsPointUpdate) {
                const featureToSend = state.mapLatestPointData.features.find(
                    (f) => f.id === `latest-${message.data?.payload_id}`
                );
                updateMapSource("map-latest-points", state.mapLatestPointData);
            }
            // Save cache if any relevant data changed
            if (updateResult.needsLineUpdate || updateResult.needsPointUpdate) {
                cache.saveDataToCache();
            }
        } catch (error) {
            console.error(
                "Failed to parse/handle WebSocket message:",
                event.data,
                error
            );
        }
    };

    state.webSocket.onerror = (error) => {
        console.error("WebSocket Error Event:", error);
        // Don't set status to 'error' here, onclose will handle retries/final state
    };

    state.webSocket.onclose = (event) => {
        console.log(
            `WebSocket Closed: Code=${event.code}, Reason=${event.reason}, WasClean=${event.wasClean}`
        );
        state.webSocket = null; // Clear the instance from state

        // Only transition to disconnected if not already in a specific error state (e.g., map init failed)
        if (state.connectionStatus !== "error") {
            state.connectionStatus = "disconnected";
        }
        ui.updateStatusIndicator(); // Update UI reflecting disconnected or error state

        // --- Exponential Backoff Retry Logic ---
        const maxRetries = 10; // Max number of automatic retries
        if (
            !event.wasClean &&
            state.retryCount < maxRetries &&
            state.connectionStatus !== "error"
        ) {
            // Calculate delay: 1s, 2s, 4s, 8s, ..., up to 60s
            const delay = Math.min(1000 * 2 ** state.retryCount, 60000);
            state.retryCount++; // Increment attempt counter *before* scheduling
            console.log(
                `Scheduling WebSocket reconnect attempt ${
                    state.retryCount
                } in ${delay / 1000}s...`
            );

            state.retryTimeout = window.setTimeout(() => {
                connectWebSocket(); // Attempt reconnect
            }, delay);
        } else if (state.retryCount >= maxRetries) {
            console.error(
                `WebSocket reconnect failed after ${maxRetries} attempts. Stopping automatic retries.`
            );
            state.connectionStatus = "error"; // Show permanent error after max retries
            ui.updateStatusIndicator();
            state.retryCount = 0; // Reset count after stopping
        } else {
            // Clean close, manual disconnect, or already in error state - reset retry count
            console.log(
                "WebSocket closed cleanly, or retries stopped/unnecessary. Resetting retry count."
            );
            state.retryCount = 0;
            // Ensure timer is cleared if we stopped retrying for other reasons
            if (state.retryTimeout !== null) {
                clearTimeout(state.retryTimeout);
                state.retryTimeout = null;
            }
        }
    };
}

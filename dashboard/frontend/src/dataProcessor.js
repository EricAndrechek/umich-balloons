// src/dataProcessor.js

import { state } from "./state.js";

// --- Color Assignment ---
// Simple hashing function to get a consistent color for a payload ID
export function getColorForPayload(payloadId) {
    let hash = 0;
    const idStr = String(payloadId); // Ensure string representation
    for (let i = 0; i < idStr.length; i++) {
        hash = idStr.charCodeAt(i) + ((hash << 5) - hash);
    }
    // Generate a hex color code from the hash
    let color = "#";
    for (let i = 0; i < 3; i++) {
        const value = (hash >> (i * 8)) & 0xff;
        color += value.toString(16).padStart(2, "0");
    }
    // Ensure brightness/saturation? Maybe later.
    return color;
}

/**
 * Processes incoming LineString segments (initial or catchup).
 * Updates state.mapLineData and state.latestCoords directly.
 * @param {GeoJSON.FeatureCollection<GeoJSON.LineString>} segmentCollection - Feature collection of path segments.
 * @returns {boolean} - True if state.mapLineData was changed, false otherwise.
 */
export function processPathSegments(segmentCollection) {
    console.log(
        `Processing ${segmentCollection.features.length} path segments...`
    );
    let needsLineUpdate = false;
    let needsPointUpdate = false;

    // --- Step 1 & 2: Identify Payloads and Remove Existing Line Features ---
    // (Keep the logic from the previous step to prevent duplicate lines)
    const payloadIdsInUpdate = new Set();
    segmentCollection.features.forEach((feature) => {
        if (feature.properties?.payload_id != null) {
            payloadIdsInUpdate.add(feature.properties.payload_id);
        }
    });

    if (payloadIdsInUpdate.size > 0) {
        const originalFeatureCount = state.mapLineData.features.length;
        state.mapLineData.features = state.mapLineData.features.filter(
            (feature) => !payloadIdsInUpdate.has(feature.properties?.payload_id)
        );
        if (state.mapLineData.features.length < originalFeatureCount) {
            needsLineUpdate = true; // Mark lines changed if features were removed
            console.log(
                `Removed existing line features for payloads:`,
                Array.from(payloadIdsInUpdate)
            );
        }
    }

    // --- Step 3: Add the NEW segments from the incoming collection ---
    segmentCollection.features.forEach((segmentFeature) => {
        const props = segmentFeature.properties;
        const geometry = segmentFeature.geometry;

        // We know payload_id exists from the construction phase in websocket.js
        const payloadId = props.payload_id;
        const color = getColorForPayload(payloadId);

        // --- Handle based on Geometry Type ---
        if (geometry?.type === "LineString") {
            // --- Process LineString ---
            if (
                !Array.isArray(geometry.coordinates) ||
                geometry.coordinates.length < 2
            ) {
                console.warn(
                    `Skipping invalid LineString geometry for payload ${payloadId}:`,
                    geometry
                );
                return; // Skip this feature
            }

            // Add the feature to mapLineData
            // Generate ID (same as before)
            const firstCoordStr = geometry.coordinates[0]
                .map((c) => c.toFixed(6))
                .join(",");
            const featureId = `segment-${payloadId}-${firstCoordStr}`;
            // Use properties from the feature passed in
            const lineFeature = {
                ...segmentFeature,
                id: featureId,
                properties: { ...props, color: color },
            };

            state.mapLineData.features.push(lineFeature);
            needsLineUpdate = true; // Mark state changed

            // Update latestCoords with the *end* point of this LineString
            const lastCoord =
                geometry.coordinates[geometry.coordinates.length - 1];
            if (
                Array.isArray(lastCoord) &&
                lastCoord.length === 2 &&
                typeof lastCoord[0] === "number" &&
                typeof lastCoord[1] === "number"
            ) {
                // TODO: Add timestamp comparison logic if desired/possible using props.last_point_time
                state.latestCoords.set(payloadId, lastCoord);
            } else {
                console.warn(
                    `Segment ${featureId} for payload ${payloadId} has invalid last coordinate.`
                );
            }
            // Point data (dot) will be updated below using the same lastCoord if it's the latest overall info
        } else if (geometry?.type === "Point") {
            // --- Process Point ---
            console.log(`Processing feature as Point for payload ${payloadId}`);
            if (
                !Array.isArray(geometry.coordinates) ||
                geometry.coordinates.length !== 2 ||
                typeof geometry.coordinates[0] !== "number" ||
                typeof geometry.coordinates[1] !== "number"
            ) {
                console.warn(
                    `Skipping invalid Point geometry for payload ${payloadId}:`,
                    geometry
                );
                return; // Skip this feature
            }
            const pointCoord = geometry.coordinates; // [lon, lat]

            // Update latestCoords with this Point's coordinate
            // TODO: Add timestamp comparison logic if desired/possible using props.last_point_time (or similar property)
            state.latestCoords.set(payloadId, pointCoord);

            // --- Update Latest Point Marker Data (like in handleNewPosition) ---
            const latestPointFeatureId = `latest-${payloadId}`;
            const latestPointFeatureIndex =
                state.mapLatestPointData.features.findIndex(
                    (f) => f.id === latestPointFeatureId
                );

            // Use timestamp from the segment data if available (e.g., last_point_time)
            const pointTimestamp =
                props.last_point_time ||
                props.first_point_time ||
                props.timestamp; // Get best available time

            const pointProperties = {
                payload_id: payloadId,
                timestamp: pointTimestamp, // Use timestamp from segment data
                color: color,
                // Get callsign if already cached
                ...(state.payloadDetailsCache.has(payloadId) && {
                    callsign:
                        state.payloadDetailsCache.get(payloadId).balloon_name ||
                        `ID: ${payloadId}`,
                }),
                // Add other properties if needed
            };
            const newLatestPointFeature = {
                type: "Feature",
                id: latestPointFeatureId,
                properties: pointProperties,
                geometry: { type: "Point", coordinates: pointCoord }, // Use the Point's coordinates
            };

            if (latestPointFeatureIndex > -1) {
                state.mapLatestPointData.features[latestPointFeatureIndex] =
                    newLatestPointFeature;
            } else {
                state.mapLatestPointData.features.push(newLatestPointFeature);
            }
            needsPointUpdate = true; // Mark points changed
        } else {
            // --- Handle other geometry types or invalid data ---
            console.warn(
                `Skipping feature with unhandled geometry type "${geometry?.type}" for payload ${payloadId}`
            );
            return; // Skip
        }
    }); // End forEach segmentFeature

    // --- Step 4: Update state references if needed ---
    if (needsLineUpdate) {
        state.mapLineData = {
            ...state.mapLineData,
            features: [...state.mapLineData.features],
        };
    }
    // IMPORTANT: Also update mapLatestPointData reference if points changed
    if (needsPointUpdate) {
        state.mapLatestPointData = {
            ...state.mapLatestPointData,
            features: [...state.mapLatestPointData.features],
        };
    }

    return needsLineUpdate; // Return true if any features were removed or added
}

/**
 * Handles a single real-time position update.
 * Updates state.mapLineData (appends to existing line), state.mapLatestPointData, and state.latestCoords.
 * @param {object} posData - The position data object { payload_id, lon, lat, timestamp? }
 * @returns {{needsLineUpdate: boolean, needsPointUpdate: boolean}} - Flags indicating which state parts were updated.
 */
export function handleNewPosition(posData) {
    // Basic validation
    if (
        posData?.payload_id == null ||
        posData.lon == null ||
        posData.lat == null
    ) {
        console.warn("Skipping invalid position data:", posData);
        return { needsLineUpdate: false, needsPointUpdate: false };
    }

    const payloadId = posData.payload_id;
    const newCoord = [posData.lon, posData.lat];
    const newTimestamp = posData.timestamp;
    const color = getColorForPayload(payloadId);
    let needsLineUpdate = false;
    let needsPointUpdate = false;

    // --- Update LineString ---
    const lastCoord = state.latestCoords.get(payloadId); // Get the most recent coordinate
    const lineFeatureId = `line-${payloadId}`; // ID for the *dynamically extended* line, separate from historical segments
    let lineFeatureIndex = state.mapLineData.features.findIndex(
        (f) => f.id === lineFeatureId
    );
    let lineFeature =
        lineFeatureIndex > -1
            ? state.mapLineData.features[lineFeatureIndex]
            : null;

    // Ensure we are working with a valid LineString feature meant for dynamic updates
    if (lineFeature && lineFeature.geometry?.type !== "LineString") {
        console.warn(
            `Found feature with ID ${lineFeatureId} but it's not a LineString. Resetting.`
        );
        lineFeature = null; // Ignore it, will create a new one if possible
        // Remove the invalid feature?
        state.mapLineData.features.splice(lineFeatureIndex, 1);
        lineFeatureIndex = -1; // Reset index
        needsLineUpdate = true; // Need to reflect removal
    }

    if (lineFeature) {
        // Append to existing dynamic line, avoiding duplicates
        const currentCoords = lineFeature.geometry.coordinates;
        const lastInLine = currentCoords[currentCoords.length - 1];
        if (
            lastInLine[0].toFixed(6) !== newCoord[0].toFixed(6) ||
            lastInLine[1].toFixed(6) !== newCoord[1].toFixed(6)
        ) {
            // Use tolerance
            currentCoords.push(newCoord); // Append directly
            needsLineUpdate = true;
            // console.debug(`Appended coord to dynamic line ${lineFeatureId}`);
        }
    } else if (lastCoord) {
        // Dynamic line doesn't exist, but we have a previous point -> Create it
        // This line starts from the last known point (from segment or previous update)
        // console.debug(`Creating new dynamic line ${lineFeatureId} from lastCoord`);
        lineFeature = {
            type: "Feature",
            id: lineFeatureId, // Use the specific ID
            properties: { payload_id: payloadId, color: color, dynamic: true }, // Mark as dynamic
            geometry: {
                type: "LineString",
                coordinates: [lastCoord, newCoord],
            }, // Start line from last known to new
        };
        state.mapLineData.features.push(lineFeature);
        needsLineUpdate = true;
    } // else: First point for this payload, cannot draw line yet.

    // --- Update latestCoords (always, this is the newest point) ---
    state.latestCoords.set(payloadId, newCoord);
    // TODO: Optionally store timestamp with latestCoords if needed for comparison logic

    const latestPointFeatureId = `latest-${payloadId}`;
    const latestPointFeatureIndex = state.mapLatestPointData.features.findIndex(
        (f) => f.id === latestPointFeatureId
    );
    // Create properties object (same as before)
    const pointProperties = {
        payload_id: payloadId,
        timestamp: newTimestamp,
        color: color,
        // Add 'callsign' property IF already available in cache (for consistency)
        ...(state.payloadDetailsCache.has(payloadId) && {
            callsign:
                state.payloadDetailsCache.get(payloadId).balloon_name ||
                `ID: ${payloadId}`,
        }),
        ...(posData.telemetry && { telemetry: posData.telemetry }),
    };

    if (latestPointFeatureIndex > -1) {
        // --- Modify existing feature IN PLACE ---
        const existingFeature =
            state.mapLatestPointData.features[latestPointFeatureIndex];
        // Update geometry coordinates directly
        existingFeature.geometry.coordinates = newCoord;
        // Update properties directly
        existingFeature.properties = pointProperties;
        // NOTE: We are NOT replacing the feature object reference in the array here.
    } else {
        // --- New payload ID - Create and Push (Same as before) ---
        const newLatestPointFeature = {
            type: "Feature",
            id: latestPointFeatureId,
            properties: pointProperties,
            geometry: { type: "Point", coordinates: newCoord },
        };
        state.mapLatestPointData.features.push(newLatestPointFeature);
    }
    needsPointUpdate = true; // Still true

    // Create new object references for reactivity if state was modified
    if (needsLineUpdate) {
        state.mapLineData = {
            ...state.mapLineData,
            features: [...state.mapLineData.features], // New array reference
        };
    }
    if (needsPointUpdate) {
        // *** Still create a new collection reference ***
        state.mapLatestPointData = {
            ...state.mapLatestPointData,
            // *** Use spread operator to ensure features array reference also changes ***
            features: [...state.mapLatestPointData.features],
        };
    }

    return { needsLineUpdate, needsPointUpdate };
}
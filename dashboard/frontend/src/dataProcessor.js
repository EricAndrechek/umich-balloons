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
 * Adds coordinates from a LineString feature to the time-ordered store.
 * Uses last_point_time + index to try and preserve order within segment.
 * @param {GeoJSON.Feature} feature - The feature containing the LineString geometry and properties.
 * @returns {string | null} payloadId if processed, null otherwise.
 */
function processLineStringFeature(feature) {
    const props = feature.properties;
    const geometry = feature.geometry;
    const payloadId = props?.payload_id;

    if (!payloadId || geometry?.type !== 'LineString' || !Array.isArray(geometry.coordinates) || geometry.coordinates.length < 1) {
        console.warn("Skipping invalid LineString feature in processLineStringFeature", feature);
        return null;
    }

    // Use last_point_time as the base timestamp for this segment's points
    const baseTimeStr = props.last_point_time; // Or props.time_bin_start ? Need consistent reference
    if (!baseTimeStr) {
        console.warn(`LineString feature for ${payloadId} missing reliable timestamp (e.g., last_point_time). Skipping.`);
        return null; // Cannot place points accurately in time
    }
    const baseTimeMs = new Date(baseTimeStr).getTime();
    if (isNaN(baseTimeMs)) {
         console.warn(`Invalid base timestamp '${baseTimeStr}' for ${payloadId}. Skipping.`);
         return null;
    }


    const coordsMap = state.payloadCoordsByTime.get(payloadId) || new Map();
    let pointsAdded = 0;

    geometry.coordinates.forEach((coord, index) => {
        if (Array.isArray(coord) && coord.length === 2 && typeof coord[0] === 'number' && typeof coord[1] === 'number') {
            // Use base time + index as a pseudo-timestamp to maintain order within segment
            // Add small offset (e.g., index * 10ms) to avoid collisions IF baseTimeMs is exact end time
            // If baseTimeMs is start time, maybe use index / length * duration? Simpler: index offset
            const pseudoTimestampMs = baseTimeMs + index; // Simple offset, assumes points are < 1ms apart effectively
            coordsMap.set(pseudoTimestampMs, [coord[0], coord[1]]);
            pointsAdded++;
        } else {
            console.warn(`Invalid coordinate found in LineString for ${payloadId} at index ${index}`, coord);
        }
    });

    if (pointsAdded > 0) {
        state.payloadCoordsByTime.set(payloadId, coordsMap);
        // Update latestCoords with the actual last point of this segment
        const lastCoord = geometry.coordinates[geometry.coordinates.length - 1];
         if (Array.isArray(lastCoord) && lastCoord.length === 2 && typeof lastCoord[0] === "number" && typeof lastCoord[1] === "number") {
             state.latestCoords.set(payloadId, lastCoord); // Still useful for immediate feedback? Maybe not needed if regen handles it. Let's keep for now.
         }
        return payloadId;
    }
    return null;
}

/**
 * Adds coordinates from a Point feature to the time-ordered store.
 * @param {GeoJSON.Feature} feature - The feature containing the Point geometry and properties.
 * @returns {string | null} payloadId if processed, null otherwise.
 */
function processPointFeature(feature) {
    const props = feature.properties;
    const geometry = feature.geometry;
    const payloadId = props?.payload_id;

    if (!payloadId || geometry?.type !== 'Point' || !Array.isArray(geometry.coordinates) || geometry.coordinates.length !== 2 || typeof geometry.coordinates[0] !== 'number' || typeof geometry.coordinates[1] !== 'number') {
        console.warn("Skipping invalid Point feature in processPointFeature", feature);
        return null;
    }

    const coord = geometry.coordinates;
    // Use the best available timestamp
    const timeStr = props.last_point_time || props.first_point_time || props.ts;
     if (!timeStr) {
         console.warn(`Point feature for ${payloadId} missing reliable timestamp. Skipping.`);
         return null; // Cannot place point accurately in time
     }
     const timeMs = new Date(timeStr).getTime();
     if (isNaN(timeMs)) {
          console.warn(`Invalid timestamp '${timeStr}' for Point feature ${payloadId}. Skipping.`);
          return null;
     }

    const coordsMap = state.payloadCoordsByTime.get(payloadId) || new Map();
    coordsMap.set(timeMs, [coord[0], coord[1]]);
    state.payloadCoordsByTime.set(payloadId, coordsMap);
    state.latestCoords.set(payloadId, coord); // Update latest known coord

    return payloadId;
}

/**
 * Processes features constructed from backend segment messages.
 * Adds coordinates to the time-ordered store state.payloadCoordsByTime.
 * Does NOT modify mapLineData directly.
 * @param {GeoJSON.FeatureCollection} featureCollection - Features constructed from backend message.
 * @returns {Set<string>} - A Set of payload IDs that were updated.
 */
export function processIncomingSegments(featureCollection) {
    console.log(
        `Processing ${featureCollection.features.length} features from segment message...`
    );
    const updatedPayloads = new Set();

    // --- Step 1: Identify Payloads in this Update ---
    const payloadIdsInUpdate = new Set();
    featureCollection.features.forEach((feature) => {
        if (feature.properties?.payload_id != null) {
            payloadIdsInUpdate.add(feature.properties.payload_id);
        } // else { // Warning might have been logged during feature construction }
    });

    // --- Step 2: Clear Existing Coordinates for these Payloads in the Store ---
    // This prevents duplicates when catchup data overlaps with cache or previous updates
    if (payloadIdsInUpdate.size > 0) {
        console.log(
            `Clearing existing coordinate store entries for payloads:`,
            payloadIdsInUpdate
        );
        for (const payloadId of payloadIdsInUpdate) {
            // Get the existing map for the payload, if it exists
            const coordsMap = state.payloadCoordsByTime.get(payloadId);
            if (coordsMap) {
                // Clear all coordinates previously stored for this payload
                // This assumes the incoming message provides the complete, authoritative
                // data for the time period it represents.
                coordsMap.clear();
                // Note: We don't need to call state.payloadCoordsByTime.set here,
                // as the map reference is still the same, just its content is cleared.
            }
            // We might also want to clear latestCoords here, but regenerateMapFeatures will set it correctly later.
            // state.latestCoords.delete(payloadId);
        }
    } else {
        console.log("No valid payload IDs found in incoming segment message.");
        return updatedPayloads; // Exit early if no payloads to process
    }
    // --- End Clearing Step ---

    // --- Step 3: Process incoming features and add their points to the store ---
    featureCollection.features.forEach((feature) => {
        let processedPayloadId = null;
        // Use existing helper functions which add to state.payloadCoordsByTime
        if (feature.geometry?.type === "LineString") {
            processedPayloadId = processLineStringFeature(feature);
        } else if (feature.geometry?.type === "Point") {
            processedPayloadId = processPointFeature(feature);
        } else {
            // Logged during feature construction or by helpers
        }

        // Add the payload ID to the set if points were successfully added to its store
        if (processedPayloadId) {
            updatedPayloads.add(processedPayloadId);
        }
    });

    console.log(
        `Finished processing segments. Payloads updated in store:`,
        updatedPayloads
    );
    return updatedPayloads; // Return set of payloads whose stores were modified
}

/**
 * Processes a new position update.
 * Adds coordinate to the time-ordered store state.payloadCoordsByTime.
 * Does NOT modify mapLineData directly.
 * @param {object} posData - The position data object { payload_id, lon, lat, timestamp? }
 * @returns {Set<string>} - A Set containing the updated payloadId (or empty if invalid).
 */
export function handleNewPosition(posData) {
    const updatedPayloads = new Set();
    if (
        posData?.payload_id == null ||
        posData.longitude == null ||
        posData.latitude == null ||
        (!posData.ts && !posData.data_time)
    ) {
        console.warn("Skipping invalid/untimestamped position data:", posData);
        return updatedPayloads;
    }

    console.log("New position: ", posData);
    const payloadId = posData.payload_id;
    const coord = [posData.longitude, posData.latitude];
    let timeMs = new Date(posData.data_time).getTime();
    const cepValue = posData.cep || 10; // default to 10 m if not provided

    if (isNaN(timeMs)) {
        timeMs = new Date(posData.ts).getTime();
        if (isNaN(timeMs)) {
            console.warn(
                `Invalid timestamp '${posData.ts}' for new position ${payloadId}. Skipping.`
            );
            return updatedPayloads;
        }
    }

    const coordsMap = state.payloadCoordsByTime.get(payloadId) || new Map();
    coordsMap.set(timeMs, coord);
    state.payloadCoordsByTime.set(payloadId, coordsMap);
    state.latestCoords.set(payloadId, coord); // Update latest known coord

    // // Option 1: Store latest CEP alongside latestCoords (simpler integration)
    // // We modify latestCoords slightly to store an object: Map<payloadId, {coord: [lon, lat], cep: number | null}>
    // let latestInfo = state.latestCoords.get(payloadId) || {};
    // latestInfo = {
    //     coord: coord,
    //     cep: cepValue != null ? Number(cepValue) : null,
    // }; // Store cep as number or null
    // state.latestCoords.set(payloadId, latestInfo);

    updatedPayloads.add(payloadId);
    // console.log(`handleNewPosition added point for ${payloadId} at time ${timeMs}`); // Debug log
    return updatedPayloads;
}


/**
 * Regenerates LineString and Point features for the map based on sorted coordinates
 * for the specified payloads. Updates state.mapLineData and state.mapLatestPointData.
 * @param {Set<string>} payloadIdsToUpdate - Set of payload IDs whose features need regeneration.
 * @returns {{needsLineUpdate: boolean, needsPointUpdate: boolean}}
 */
export function regenerateMapFeatures(payloadIdsToUpdate) {
    if (!payloadIdsToUpdate || payloadIdsToUpdate.size === 0) {
        return { needsLineUpdate: false, needsPointUpdate: false };
    }
    console.log(`Regenerating map features for payloads:`, payloadIdsToUpdate);

    let linesChanged = false;
    let pointsChanged = false;

    // Keep track of features we don't need to regenerate
    const finalLineFeatures = state.mapLineData.features.filter(f => !payloadIdsToUpdate.has(f.properties?.payload_id));
    const finalPointFeatures = state.mapLatestPointData.features.filter(f => !payloadIdsToUpdate.has(f.properties?.payload_id));


    for (const payloadId of payloadIdsToUpdate) {
        const coordsMap = state.payloadCoordsByTime.get(payloadId);

        if (!coordsMap || coordsMap.size === 0) {
            console.log(`No coordinates found for ${payloadId} during regeneration.`);
            // Ensure latestCoords is also cleared if no data
             state.latestCoords.delete(payloadId);
            continue; // No features to generate for this payload
        }

        // --- Regenerate Line Feature ---
        // Sort coordinates by timestamp (the Map key)
        const sortedEntries = Array.from(coordsMap.entries()).sort((a, b) => a[0] - b[0]);
        const sortedCoords = sortedEntries.map(entry => entry[1]); // Get just [[lon, lat], ...]

        if (sortedCoords.length >= 2) {
            const lineFeatureId = `merged-line-${payloadId}`;
            const color = getColorForPayload(payloadId); // Get color
            const newLineFeature = {
                type: "Feature",
                id: lineFeatureId,
                properties: {
                    payload_id: payloadId,
                    color: color,
                    point_count: sortedCoords.length // Add potentially useful property
                },
                geometry: {
                    type: "LineString",
                    coordinates: sortedCoords,
                },
            };
            finalLineFeatures.push(newLineFeature);
            linesChanged = true; // Line data changed
        }

        // --- Regenerate Point Feature (using the latest coordinate) ---
        if (sortedCoords.length > 0) {

            const latestEntry = sortedEntries[sortedEntries.length - 1];
            const latestTimestampMs = latestEntry[0];
            const latestCoord = latestEntry[1];

            // --- Update latestCoords state (with CEP) ---
            // Retrieve the CEP stored when handleNewPosition last ran for this point's timestamp.
            // This requires relating latestTimestampMs back to the info stored in handleNewPosition.
            // Simpler: Use the latest CEP stored alongside latestCoords.
            const latestInfo = state.latestCoords.get(payloadId); // Should be {coord: latestCoord, cep: ...}
            const cepValue = latestInfo?.cep; // Get CEP from the stored info

            // Update latestCoords state value (redundant if already updated, but safe)
            state.latestCoords.set(payloadId, {
                coord: latestCoord,
                cep: cepValue,
            });

            // --- Create/Update Point Feature ---
            const pointFeatureId = `latest-${payloadId}`;
            const color = getColorForPayload(payloadId);
            const details = state.payloadDetailsCache.get(payloadId);
            const pointProperties = {
                payload_id: payloadId,
                timestamp: new Date(latestTimestampMs).toISOString(),
                color: color,
                callsign: details?.balloon_name || `ID: ${payloadId}`,
                // --- Add CEP property to the feature ---
                ...(cepValue != null && { cep: cepValue }), // Add cep only if it exists and is not null/undefined
            };
            const newPointFeature = {
                type: "Feature",
                id: pointFeatureId,
                properties: pointProperties,
                geometry: { type: "Point", coordinates: latestCoord },
            };
            finalPointFeatures.push(newPointFeature);
            pointsChanged = true;
        } else {
            state.latestCoords.delete(payloadId);
        }
    } // End loop through payloadIdsToUpdate

    // --- Update State Collections ---
    let finalLinesChanged = linesChanged || (state.mapLineData.features.length !== finalLineFeatures.length);
    let finalPointsChanged = pointsChanged || (state.mapLatestPointData.features.length !== finalPointFeatures.length);

    // Assign the regenerated features
    state.mapLineData.features = finalLineFeatures;
    state.mapLatestPointData.features = finalPointFeatures;

    // Create new top-level references if anything changed
    if (finalLinesChanged) {
        state.mapLineData = { ...state.mapLineData, features: [...state.mapLineData.features] };
    }
    if (finalPointsChanged) {
        state.mapLatestPointData = { ...state.mapLatestPointData, features: [...state.mapLatestPointData.features] };
    }

    console.log(`Regeneration finished. Lines changed: ${finalLinesChanged}, Points changed: ${finalPointsChanged}`);
    return { needsLineUpdate: finalLinesChanged, needsPointUpdate: finalPointsChanged };
}

// maybe not needed:

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
                props.ts; // Get best available time

            const pointProperties = {
                payload_id: payloadId,
                ts: pointTimestamp, // Use timestamp from segment data
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

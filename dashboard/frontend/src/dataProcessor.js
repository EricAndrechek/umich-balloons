// src/dataProcessor.js

import * as state from "./state.js";

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

    segmentCollection.features.forEach((segmentFeature) => {
        const props = segmentFeature.properties;
        const geometry = segmentFeature.geometry;

        // Basic validation
        if (
            !props ||
            props.payload_id == null || // Allow 0
            geometry?.type !== "LineString" ||
            !Array.isArray(geometry.coordinates) ||
            geometry.coordinates.length < 2 ||
            !Array.isArray(geometry.coordinates[0]) ||
            geometry.coordinates[0].length !== 2 // Check coordinate structure
        ) {
            console.warn("Skipping invalid segment feature:", segmentFeature);
            return;
        }

        const payloadId = props.payload_id;
        const color = getColorForPayload(payloadId);
        // Ensure unique feature ID using first point coordinates
        const firstCoordStr = geometry.coordinates[0]
            .map((c) => c.toFixed(6))
            .join(","); // Use fixed precision string
        const featureId =
            segmentFeature.id ?? `segment-${payloadId}-${firstCoordStr}`;

        const featureProperties = { payload_id: payloadId, color: color };

        // Find if a feature with this specific ID already exists
        const existingFeatureIndex = state.mapLineData.features.findIndex(
            (f) => f.id === featureId
        );

        const newFeature = {
            type: "Feature",
            id: featureId,
            properties: featureProperties,
            geometry: geometry,
        };

        if (existingFeatureIndex > -1) {
            // Optimization: Avoid update if geometry hasn't changed
            if (
                JSON.stringify(
                    state.mapLineData.features[existingFeatureIndex].geometry
                ) !== JSON.stringify(newFeature.geometry)
            ) {
                state.mapLineData.features[existingFeatureIndex] = newFeature; // Replace existing
                needsLineUpdate = true;
            }
        } else {
            state.mapLineData.features.push(newFeature); // Add new
            needsLineUpdate = true;
        }

        // --- Record the LAST point of this segment for potential line extension ---
        const lastCoord = geometry.coordinates[geometry.coordinates.length - 1];
        if (
            Array.isArray(lastCoord) &&
            lastCoord.length === 2 &&
            typeof lastCoord[0] === "number" &&
            typeof lastCoord[1] === "number"
        ) {
            // Update latestCoords only if this segment seems newer (needs timestamp ideally)
            // Simple approach: always update with the last point of any processed segment.
            // console.debug(`Updating latestCoord for ${payloadId} from segment ${featureId} to:`, lastCoord);
            state.latestCoords.set(payloadId, lastCoord);
        } else {
            console.warn(
                `Segment ${featureId} for payload ${payloadId} has invalid last coordinate.`
            );
        }
    });

    if (needsLineUpdate) {
        // Create a new object reference for the feature collection to ensure reactivity
        state.mapLineData = {
            ...state.mapLineData,
            features: [...state.mapLineData.features],
        };
    }
    return needsLineUpdate;
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
    const lastCoord = state.latestCoords.get(payloadId);
    const lineFeatureId = `line-${payloadId}`; // ID for the dynamically extended line
    let lineFeatureIndex = state.mapLineData.features.findIndex(
        (f) => f.id === lineFeatureId
    );
    let lineFeature =
        lineFeatureIndex > -1
            ? state.mapLineData.features[lineFeatureIndex]
            : null;

    if (lineFeature && lineFeature.geometry.type === "LineString") {
        const lastInLine =
            lineFeature.geometry.coordinates[
                lineFeature.geometry.coordinates.length - 1
            ];
        if (lastInLine[0] !== newCoord[0] || lastInLine[1] !== newCoord[1]) {
            lineFeature.geometry.coordinates.push(newCoord); // Append directly
            needsLineUpdate = true;
            // console.debug(`Appended coord to existing dynamic line ${lineFeatureId}`);
        } // else: Skip duplicate coord
    } else if (lastCoord) {
        // Dynamic line doesn't exist, but we have a previous point -> Create it
        // console.debug(`Creating new dynamic line ${lineFeatureId} from lastCoord`);
        lineFeature = {
            type: "Feature",
            id: lineFeatureId,
            properties: { payload_id: payloadId, color: color, dynamic: true },
            geometry: {
                type: "LineString",
                coordinates: [lastCoord, newCoord],
            },
        };
        state.mapLineData.features.push(lineFeature);
        needsLineUpdate = true;
    } // else: First point for this payload, cannot draw line yet.

    // --- Update latestCoords (always) ---
    state.latestCoords.set(payloadId, newCoord);

    // --- Update Latest Point Marker ---
    const latestPointFeatureId = `latest-${payloadId}`;
    const latestPointFeatureIndex = state.mapLatestPointData.features.findIndex(
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
        state.mapLatestPointData.features[latestPointFeatureIndex] =
            newLatestPointFeature;
    } else {
        state.mapLatestPointData.features.push(newLatestPointFeature);
    }
    needsPointUpdate = true; // Always update point data on new position

    // Create new object references for reactivity if state was modified
    if (needsLineUpdate) {
        state.mapLineData = {
            ...state.mapLineData,
            features: [...state.mapLineData.features],
        };
    }
    if (needsPointUpdate) {
        state.mapLatestPointData = {
            ...state.mapLatestPointData,
            features: [...state.mapLatestPointData.features],
        };
    }

    return { needsLineUpdate, needsPointUpdate };
}

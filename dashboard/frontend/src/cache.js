// src/cache.js

import * as state from "./state.js";
import * as config from "./config.js";

/** Saves the current map data state to localStorage */
export function saveDataToCache() {
    try {
        // Use structuredClone for potentially complex GeoJSON objects if needed,
        // but JSON.stringify is usually sufficient here.
        localStorage.setItem(
            config.CACHE_KEY_LINES,
            JSON.stringify(state.mapLineData)
        );
        localStorage.setItem(
            config.CACHE_KEY_POINTS,
            JSON.stringify(state.mapLatestPointData)
        );
        localStorage.setItem(config.CACHE_KEY_TIMESTAMP, Date.now().toString());
        console.debug("Map data saved to local cache.");
    } catch (error) {
        console.error("Error saving data to localStorage:", error);
        // Handle potential storage quota errors more gracefully if needed
    }
}

/**
 * Loads map data from localStorage if available and not expired.
 * Updates the shared state variables directly.
 * Reconstructs latestCoords map.
 * @returns {boolean} True if valid cached data was loaded, false otherwise.
 */
export function loadDataFromCache() {
    const cachedTimestampStr = localStorage.getItem(config.CACHE_KEY_TIMESTAMP);
    const cachedLinesStr = localStorage.getItem(config.CACHE_KEY_LINES);
    const cachedPointsStr = localStorage.getItem(config.CACHE_KEY_POINTS);

    if (!cachedTimestampStr || !cachedLinesStr) {
        console.log("No valid cache data found (timestamp or lines missing).");
        return false;
    }

    const cachedTimestamp = parseInt(cachedTimestampStr, 10);
    const now = Date.now();

    if (
        isNaN(cachedTimestamp) ||
        now - cachedTimestamp > config.CACHE_DURATION_MS
    ) {
        console.log("Cache data expired or timestamp invalid.");
        // Clear expired/invalid cache
        localStorage.removeItem(config.CACHE_KEY_LINES);
        localStorage.removeItem(config.CACHE_KEY_POINTS);
        localStorage.removeItem(config.CACHE_KEY_TIMESTAMP);
        return false;
    }

    try {
        const loadedLineData = JSON.parse(cachedLinesStr);
        const loadedPointData = cachedPointsStr
            ? JSON.parse(cachedPointsStr)
            : { type: "FeatureCollection", features: [] };

        // Basic validation
        if (
            loadedLineData?.type !== "FeatureCollection" ||
            !Array.isArray(loadedLineData.features) ||
            loadedPointData?.type !== "FeatureCollection" ||
            !Array.isArray(loadedPointData.features)
        ) {
            console.warn("Invalid data structure found in cache. Discarding.");
            localStorage.removeItem(config.CACHE_KEY_LINES);
            localStorage.removeItem(config.CACHE_KEY_POINTS);
            localStorage.removeItem(config.CACHE_KEY_TIMESTAMP);
            return false;
        }

        // --- Update State Directly ---
        state.mapLineData = loadedLineData;
        state.mapLatestPointData = loadedPointData;

        // --- Reconstruct latestCoords Map ---
        state.latestCoords.clear(); // Clear previous state

        // Prioritize latest points cache
        if (state.mapLatestPointData.features.length > 0) {
            state.mapLatestPointData.features.forEach((feature) => {
                if (
                    feature.geometry?.type === "Point" &&
                    feature.properties?.payload_id != null
                ) {
                    const coords = feature.geometry.coordinates;
                    if (
                        Array.isArray(coords) &&
                        coords.length === 2 &&
                        typeof coords[0] === "number" &&
                        typeof coords[1] === "number"
                    ) {
                        state.latestCoords.set(feature.properties.payload_id, [
                            coords[0],
                            coords[1],
                        ]);
                    }
                }
            });
            console.debug(
                `Reconstructed latestCoords from cached points: ${state.latestCoords.size} entries.`
            );
        } else {
            // Fallback: Reconstruct from the end of linestrings
            state.mapLineData.features.forEach((feature) => {
                if (
                    feature.geometry?.type === "LineString" &&
                    feature.properties?.payload_id != null
                ) {
                    const coords = feature.geometry.coordinates;
                    if (Array.isArray(coords) && coords.length > 0) {
                        const lastCoord = coords[coords.length - 1];
                        if (
                            Array.isArray(lastCoord) &&
                            lastCoord.length === 2 &&
                            typeof lastCoord[0] === "number" &&
                            typeof lastCoord[1] === "number"
                        ) {
                            state.latestCoords.set(
                                feature.properties.payload_id,
                                [lastCoord[0], lastCoord[1]]
                            );
                        }
                    }
                }
            });
            console.debug(
                `Reconstructed latestCoords from cached lines: ${state.latestCoords.size} entries.`
            );
        }

        console.log(
            `Successfully loaded ${state.mapLineData.features.length} line features and ${state.mapLatestPointData.features.length} point features from cache.`
        );
        return true;
    } catch (error) {
        console.error("Error parsing data from localStorage:", error);
        localStorage.removeItem(config.CACHE_KEY_LINES);
        localStorage.removeItem(config.CACHE_KEY_POINTS);
        localStorage.removeItem(config.CACHE_KEY_TIMESTAMP);
        return false;
    }
}

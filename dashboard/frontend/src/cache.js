// src/cache.js

import { state } from "./state.js";
import * as config from "./config.js";
import { regenerateMapFeatures } from "./dataProcessor.js";

// --- Cache Keys ---
const CACHE_KEY_COORDS = "mapCoordsByTimeCache"; // New key for coordinate store
const CACHE_KEY_POINTS = "mapLatestPointDataCache"; // Keep for latest points (maybe?) - OR regenerate points too? Let's regenerate.
const CACHE_KEY_DETAILS = "mapPayloadDetailsCache"; // Cache details too
const CACHE_KEY_TIMESTAMP = "mapDataCacheTimestamp"; // Keep timestamp

/** Saves the relevant state to localStorage */
export function saveDataToCache() {
    try {
        // --- Serialize payloadCoordsByTime (Map<payloadId, Map<ts, [lon, lat]>>) ---
        const serializableCoords = [];
        for (const [payloadId, coordsMap] of state.payloadCoordsByTime.entries()) {
             // Convert inner Map to array [timestamp, [lon, lat]]
             const coordsArray = Array.from(coordsMap.entries());
             serializableCoords.push([payloadId, coordsArray]);
        }
        localStorage.setItem(CACHE_KEY_COORDS, JSON.stringify(serializableCoords));

        // --- Serialize payloadDetailsCache (Map<payloadId, detailsObject>) ---
        const serializableDetails = Array.from(state.payloadDetailsCache.entries());
        localStorage.setItem(CACHE_KEY_DETAILS, JSON.stringify(serializableDetails));

        // --- Save timestamp ---
        localStorage.setItem(CACHE_KEY_TIMESTAMP, Date.now().toString());
        console.debug("Map coordinates and details saved to local cache.");

        // --- REMOVE old keys if they exist ---
        localStorage.removeItem(config.CACHE_KEY_LINES); // Old key
        localStorage.removeItem(config.CACHE_KEY_POINTS); // Old key

    } catch (error) {
        console.error("Error saving data to localStorage:", error);
    }
}

/**
 * Loads data from localStorage and reconstructs state.
 * @returns {boolean} True if valid cached data was loaded, false otherwise.
 */
export function loadDataFromCache() {
    const cachedTimestampStr = localStorage.getItem(CACHE_KEY_TIMESTAMP);
    const cachedCoordsStr = localStorage.getItem(CACHE_KEY_COORDS);
    const cachedDetailsStr = localStorage.getItem(CACHE_KEY_DETAILS);

    if (!cachedTimestampStr || !cachedCoordsStr) { // Require timestamp and coords cache
        console.log("No valid cache data found (timestamp or coords missing).");
        clearCache(); // Clear potentially partial cache
        return false;
    }

    const cachedTimestamp = parseInt(cachedTimestampStr, 10);
    const now = Date.now();

    if (isNaN(cachedTimestamp) || now - cachedTimestamp > config.CACHE_DURATION_MS) {
        console.log("Cache data expired or timestamp invalid.");
        clearCache();
        return false;
    }

    try {
        // --- Deserialize payloadCoordsByTime ---
        const parsedCoordsArray = JSON.parse(cachedCoordsStr);
        state.payloadCoordsByTime.clear(); // Clear existing
        const loadedPayloadIds = new Set();
        if (Array.isArray(parsedCoordsArray)) {
            parsedCoordsArray.forEach(([payloadId, coordsArray]) => {
                if (payloadId && Array.isArray(coordsArray)) {
                    // Convert array back to Map
                    state.payloadCoordsByTime.set(payloadId, new Map(coordsArray));
                    loadedPayloadIds.add(payloadId);
                }
            });
        } else {
             throw new Error("Cached coordinate data is not an array.");
        }

         // --- Deserialize payloadDetailsCache ---
         state.payloadDetailsCache.clear(); // Clear existing
         if (cachedDetailsStr) {
              const parsedDetailsArray = JSON.parse(cachedDetailsStr);
              if (Array.isArray(parsedDetailsArray)) {
                   parsedDetailsArray.forEach(([payloadId, details]) => {
                        if (payloadId && details) {
                             state.payloadDetailsCache.set(payloadId, details);
                        }
                   });
              }
              // Allow details cache to be missing or invalid without failing load
         }


        // --- Regenerate Map Features from loaded coords ---
        console.log(`Cache loaded. Regenerating map features for ${loadedPayloadIds.size} payloads...`);
        if (loadedPayloadIds.size > 0) {
            // Regenerate features for ALL loaded payloads to initialize map state
            regenerateMapFeatures(loadedPayloadIds);
            console.log("Map features regenerated from cache.");
        } else {
             // Ensure map data is empty if no coords loaded
             state.mapLineData = { type: "FeatureCollection", features: [] };
             state.mapLatestPointData = { type: "FeatureCollection", features: [] };
             state.latestCoords.clear();
        }

        console.log(`Successfully loaded state from cache. Coords: ${state.payloadCoordsByTime.size}, Details: ${state.payloadDetailsCache.size}`);
        return true;

    } catch (error) {
        console.error("Error parsing data from localStorage:", error);
        clearCache(); // Clear corrupted cache
        return false;
    }
}

// Helper to clear all cache keys
function clearCache() {
     localStorage.removeItem(CACHE_KEY_COORDS);
     localStorage.removeItem(CACHE_KEY_DETAILS);
     localStorage.removeItem(CACHE_KEY_TIMESTAMP);
     localStorage.removeItem(config.CACHE_KEY_LINES); // Clear old keys too
     localStorage.removeItem(config.CACHE_KEY_POINTS);
     console.log("Cleared map cache.");
}
// src/state.js

/**
 * Shared application state.
 * Modules import these variables and can modify them directly.
 */

/** @type {import('maplibre-gl').Map | null} */
export let map = null;
/** @type {WebSocket | null} */
export let webSocket = null;
/** @type {'connecting' | 'connected' | 'disconnected' | 'error'} */
export let connectionStatus = "disconnected";
/** @type {number | null} */
export let retryTimeout = null;
export let retryCount = 0;

// Holds LineString features (from cache/server/dynamic updates)
export let mapLineData = { type: "FeatureCollection", features: [] };
// Holds ONLY the latest Point feature for each payload
export let mapLatestPointData = { type: "FeatureCollection", features: [] };
// Keep track of the latest known coordinate *per payload* for extending lines
export let latestCoords = new Map(); // Map<payload_id, [lon, lat]>

// --- State Modifier Functions ---
// It can be cleaner to use functions to modify state,
// but for closer parity with the original structure,
// we'll allow direct modification via exported 'let' variables for now.
// Example setter (if preferred): export function setMap(newMapInstance) { map = newMapInstance; }

// src/state.js

export const state = {
    // ... (existing state variables: map, webSocket, connectionStatus, etc.) ...
    map: null,
    webSocket: null,
    connectionStatus: "disconnected",
    retryTimeout: null,
    retryCount: 0,
    mapLineData: { type: "FeatureCollection", features: [] },
    mapLatestPointData: { type: "FeatureCollection", features: [] },
    latestCoords: new Map(),

    // --- New State Variables ---
    /** Stores fetched details: Map<payload_id, detailsObject> */
    payloadDetailsCache: new Map(),
    /** Tracks pending detail requests: Set<payload_id> */
    pendingDetailRequests: new Set(),
    // --- End New State Variables ---
};

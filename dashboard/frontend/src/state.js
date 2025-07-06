// src/state.js

export const state = {
    map: null,
    webSocket: null,
    connectionStatus: "disconnected",
    retryTimeout: null,
    retryCount: 0,

    // --- Data Stores ---
    /** Raw coordinate store: Map<payloadId, Map<timestamp_ms_adjusted, [lon, lat]>> */
    payloadCoordsByTime: new Map(),
    /** Cache for balloon details: Map<payload_id, detailsObject> */
    payloadDetailsCache: new Map(),
    /** Tracks pending detail requests: Set<payload_id> */
    pendingDetailRequests: new Set(),

    // --- Map Data (Generated from payloadCoordsByTime) ---
    /** Generated LineString features for map: FeatureCollection */
    mapLineData: { type: "FeatureCollection", features: [] },
    /** Generated Point features for map: FeatureCollection */
    mapLatestPointData: { type: "FeatureCollection", features: [] },
    /** Latest coordinate for extending dynamic lines/placing points: Map<payload_id, [lon, lat]> */
    latestCoords: new Map(),
};

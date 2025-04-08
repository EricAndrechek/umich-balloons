// src/config.js

// Environment determination
export const IS_PROD = location.href.startsWith("https://")
    ? true
    : import.meta.env.VITE_DEV !== "true";

// URLs based on environment
export const WS_URL = IS_PROD
    ? import.meta.env.VITE_WS_URL_PROD
    : import.meta.env.VITE_WS_URL_DEV;
export const API_URL = IS_PROD
    ? import.meta.env.VITE_API_URL_PROD
    : import.meta.env.VITE_API_URL_DEV;

// Map style
export const MAP_STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || "";

// Cache Configuration
export const CACHE_KEY_LINES = "mapLineDataCache";
export const CACHE_KEY_POINTS = "mapLatestPointDataCache";
export const CACHE_KEY_TIMESTAMP = "mapDataCacheTimestamp";
export const CACHE_DURATION_MS = 3 * 60 * 60 * 1000; // 3 hours

// Log initial config
console.debug(
    `Using ${
        IS_PROD ? "production" : "development"
    } configuration. WS_URL: ${WS_URL}, API_URL: ${API_URL}`
);
if (!MAP_STYLE_URL) {
    console.error("FATAL: Map style URL missing in config.");
}
if (!WS_URL) {
    console.error("FATAL: WebSocket URL missing in config.");
}

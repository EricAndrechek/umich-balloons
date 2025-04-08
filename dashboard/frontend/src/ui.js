// src/ui.js

import { state } from "./state.js";

// --- DOM Elements ---
export const mapContainer = document.getElementById("map");
export const statusIndicator = document.getElementById("status-indicator");

/** Updates the text and color of the status indicator based on connectionStatus */
export function updateStatusIndicator() {
    if (!statusIndicator) return;
    let text = "Offline";
    let bgColor = "bg-gray-500"; // Default: disconnected

    switch (state.connectionStatus) {
        case "connecting":
            text = "Connecting...";
            bgColor = "bg-yellow-500";
            break;
        case "connected":
            text = "Online";
            bgColor = "bg-green-500";
            break;
        case "error":
            text = "Error";
            bgColor = "bg-red-500";
            break;
        case "disconnected":
            // text = "Offline"; // Already default
            // bgColor = "bg-gray-500";
            break;
        default:
            console.warn("Unknown connection status:", state.connectionStatus);
            break;
    }
    statusIndicator.textContent = text;
    // Reset classes and apply new ones
    statusIndicator.className = `fixed bottom-4 right-4 px-3 py-1 rounded-full text-white text-sm shadow z-50 ${bgColor}`;
}

/** Placeholder for setting up other UI event listeners */
export function setupEventListeners() {
    // Add any specific button/UI listeners here if needed
    console.debug("UI Event listeners setup.");
}

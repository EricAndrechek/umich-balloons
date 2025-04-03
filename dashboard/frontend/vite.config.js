import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";
export default defineConfig({
    plugins: [
        tailwindcss(),
        VitePWA({
            registerType: "autoUpdate",
            injectRegister: "auto",
            workbox: {
                globPatterns: ["**/*.{js,css,html,ico,png,svg,json,woff2}"], // Cache static assets
                // Optional: Add runtime caching for map tiles if needed
                // runtimeCaching: [...]
            },
            manifest: {
                // Basic PWA manifest
                name: "Realtime Map Tracker (Vanilla)",
                short_name: "MapTracker VJS",
                description: "Displays real-time payload locations.",
                theme_color: "#ffffff",
                background_color: "#ffffff",
                display: "standalone",
                scope: "/",
                start_url: "/",
                icons: [
                    /* ... Define icons as in previous examples ... */
                    {
                        src: "/icon-192.png",
                        sizes: "192x192",
                        type: "image/png",
                    },
                    {
                        src: "/icon-512.png",
                        sizes: "512x512",
                        type: "image/png",
                        purpose: "any maskable",
                    },
                ],
            },
        }),
    ],
});

import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

// In dev: run `wrangler dev` (serves Worker API on :8787) + `pnpm dev` (vite on :5173).
// Vite proxies /api/* and /health to wrangler so the frontend can use relative URLs.
// In prod: a single Worker serves both the SPA (via [assets]) and the API.
export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		proxy: {
			'/api': 'http://localhost:8787',
			'/health': 'http://localhost:8787'
		}
	}
});

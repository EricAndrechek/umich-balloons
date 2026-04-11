// Pure SPA: no SSR, no prerender. The adapter's `fallback: 'index.html'`
// plus Cloudflare's `not_found_handling = "single-page-application"` means
// every route is served by index.html and resolved client-side.
export const ssr = false;
export const prerender = false;

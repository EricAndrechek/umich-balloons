import {
  type Env,
  handleHealth,
  handleAPRS,
  handleLoRa,
  handleIridium,
  handleStation,
} from "./handlers";

const MAX_BODY = 8192; // Increased for msgpack+gzip payloads

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    // Health check
    if (path === "/health" && method === "GET") {
      return handleHealth();
    }

    // All data routes require POST
    if (method !== "POST") {
      return new Response("method not allowed", { status: 405 });
    }

    // Guard body size
    const contentLength = request.headers.get("content-length");
    if (contentLength) {
      const len = parseInt(contentLength, 10);
      if (isNaN(len) || len > MAX_BODY) {
        return new Response("request body too large or invalid", { status: 413 });
      }
    }

    try {
      switch (path) {
        case "/aprs":
          return await handleAPRS(request, env);
        case "/lora":
          return await handleLoRa(request, env);
        case "/iridium":
          return await handleIridium(request, env);
        case "/station":
          return await handleStation(request, env);
        default:
          return new Response("not found", { status: 404 });
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error(`Unhandled error on ${path}: ${msg}`);
      return new Response(JSON.stringify({ error: "internal error" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
  },
} satisfies ExportedHandler<Env>;

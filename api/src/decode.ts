// Unified request body decoder.
// Handles decompression (gzip, deflate, deflate-raw) and format decoding (JSON, MessagePack).
//
// Content-Encoding: gzip|deflate|deflate-raw → decompress first (CF edge may also do this transparently)
// Content-Type: application/msgpack|application/x-msgpack → decode MessagePack
// Default: JSON

import { decode as decodeMsgpack } from "@msgpack/msgpack";

type DecompressionFormat = "gzip" | "deflate" | "deflate-raw";

const SUPPORTED_ENCODINGS: Record<string, DecompressionFormat> = {
  gzip: "gzip",
  deflate: "deflate",
  "deflate-raw": "deflate-raw",
};

export async function decodeBody(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type") || "";
  const contentEncoding = request.headers.get("content-encoding") || "";

  // 1. Read raw body
  const rawBuf = await request.arrayBuffer();

  // 2. Decompress if Content-Encoding is set
  let data: ArrayBuffer = rawBuf;
  if (contentEncoding) {
    const format = SUPPORTED_ENCODINGS[contentEncoding.trim().toLowerCase()];
    if (format) {
      try {
        const ds = new DecompressionStream(format);
        const decompressed = new Response(rawBuf).body!.pipeThrough(ds);
        data = await new Response(decompressed).arrayBuffer();
      } catch {
        // Decompression failed — CF edge may have already decompressed transparently.
        // Fall back to the raw buffer.
        data = rawBuf;
      }
    }
    // Unknown encoding: assume CF edge handled it, use raw buffer
  }

  // 3. Decode based on Content-Type
  if (contentType.includes("msgpack")) {
    return decodeMsgpack(new Uint8Array(data));
  }

  // Default: JSON
  const text = new TextDecoder().decode(data);
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("invalid JSON body");
  }
}

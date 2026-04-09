// Decodes request bodies supporting JSON and MessagePack (with optional gzip).
// Content-Type: application/msgpack → decode MessagePack
// Content-Encoding: gzip → decompress first
// Default: JSON

import { decode as decodeMsgpack } from "@msgpack/msgpack";

export async function decodeBody(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type") || "";
  const contentEncoding = request.headers.get("content-encoding") || "";

  let data: ArrayBuffer | string;

  if (contentEncoding.includes("gzip")) {
    // Cloudflare Workers automatically decompress gzip for us when
    // the request goes through CF, but for direct requests we handle it.
    // The DecompressionStream API is available in Workers.
    const body = request.body;
    if (!body) throw new Error("empty body");

    try {
      const ds = new DecompressionStream("gzip");
      const decompressed = body.pipeThrough(ds);
      const reader = decompressed.getReader();
      const chunks: Uint8Array[] = [];
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
      }
      const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
      const result = new Uint8Array(totalLength);
      let offset = 0;
      for (const chunk of chunks) {
        result.set(chunk, offset);
        offset += chunk.length;
      }
      data = result.buffer;
    } catch {
      // If DecompressionStream fails, Cloudflare may have already decompressed
      data = await request.arrayBuffer();
    }
  } else {
    data = await request.arrayBuffer();
  }

  if (contentType.includes("msgpack")) {
    const buf = data instanceof ArrayBuffer ? data : new TextEncoder().encode(data as string).buffer;
    return decodeMsgpack(new Uint8Array(buf));
  }

  // Default: JSON
  const text = typeof data === "string" ? data : new TextDecoder().decode(data as ArrayBuffer);
  return JSON.parse(text);
}

import { type Telemetry, now, formatTime, uploadToSondehub } from "./sondehub";
import { parseLoRaJSON } from "./normalize";
import { parseAPRS } from "./aprs";
import { verifyIridiumJWT } from "./jwt";

export interface Env {
  SONDEHUB_API_URL: string;
  SOFTWARE_NAME: string;
  SOFTWARE_VERSION: string;
  DEV_MODE: string;
}

function isDevMode(env: Env): boolean {
  return env.DEV_MODE === "true";
}

function jsonResponse(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function forwardToSondehub(telem: Telemetry, env: Env): Promise<Response> {
  const result = await uploadToSondehub(telem, env.SONDEHUB_API_URL, env.SOFTWARE_NAME, env.SOFTWARE_VERSION);
  if (result.status >= 200 && result.status < 300) {
    console.log(`SondeHub upload successful: callsign=${telem.payload_callsign}`);
    return jsonResponse({ status: "accepted" }, 202);
  }
  console.error(`SondeHub upload failed ${result.status}: ${result.body}`);
  return jsonResponse({ status: "upstream_error", detail: result.body }, 502);
}

// GET /health
export function handleHealth(): Response {
  return jsonResponse({ status: "ok" });
}

// POST /aprs — JSON body with raw_data field
export async function handleAPRS(request: Request, env: Env): Promise<Response> {
  const body = (await request.json()) as { sender?: string; raw_data?: string };
  if (!body.raw_data) {
    return jsonResponse({ error: "missing raw_data" }, 400);
  }

  const sender = body.sender || request.headers.get("CF-Connecting-IP") || "unknown";

  let telem: Telemetry;
  try {
    telem = parseAPRS(body.raw_data, sender);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`APRS parse error: ${msg}`);
    return jsonResponse({ error: msg }, 400);
  }

  return forwardToSondehub(telem, env);
}

// POST /aprs/raw — plain text body (raw APRS packet)
export async function handleAPRSRaw(request: Request, env: Env): Promise<Response> {
  const raw = await request.text();
  if (!raw.length) {
    return jsonResponse({ error: "empty body" }, 400);
  }

  const sender = request.headers.get("CF-Connecting-IP") || "unknown";

  let telem: Telemetry;
  try {
    telem = parseAPRS(raw, sender);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`APRS raw parse error: ${msg}`);
    return jsonResponse({ error: msg }, 400);
  }

  return forwardToSondehub(telem, env);
}

// POST /lora — JSON with raw_data (object or JSON string)
export async function handleLoRa(request: Request, env: Env): Promise<Response> {
  const body = (await request.json()) as { sender?: string; raw_data?: unknown };
  if (body.raw_data === undefined || body.raw_data === null) {
    return jsonResponse({ error: "missing raw_data" }, 400);
  }

  const sender = body.sender || request.headers.get("CF-Connecting-IP") || "unknown";

  // raw_data could be a JSON object or a JSON string containing an object
  let rawData: unknown = body.raw_data;
  if (typeof rawData === "string") {
    try {
      rawData = JSON.parse(rawData);
    } catch {
      // leave as string — parseLoRaJSON will handle it
    }
  }

  let telem: Telemetry;
  try {
    telem = parseLoRaJSON(rawData, sender);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`LoRa parse error: ${msg}`);
    return jsonResponse({ error: msg }, 400);
  }

  return forwardToSondehub(telem, env);
}

// POST /iridium — Iridium webhook with JWT verification
interface IridiumPayload {
  momsn: number;
  imei: string;
  data: string;
  serial: number;
  device_type: string;
  iridium_latitude: number;
  iridium_longitude: number;
  iridium_cep: number;
  transmit_time: string;
  JWT: string;
}

export async function handleIridium(request: Request, env: Env): Promise<Response> {
  const req = (await request.json()) as IridiumPayload;

  // Verify JWT (skip in dev mode)
  if (isDevMode(env)) {
    console.log("Iridium JWT verification skipped (dev mode)");
  } else {
    try {
      await verifyIridiumJWT(req.JWT);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn(`Iridium JWT verification failed: ${msg}`);
      return jsonResponse({ error: "JWT verification failed" }, 401);
    }
  }

  // Decode hex data
  let dataBytes: Uint8Array;
  try {
    dataBytes = hexDecode(req.data);
  } catch {
    return jsonResponse({ error: "failed to decode hex data" }, 400);
  }

  // Parse decoded data as JSON
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(new TextDecoder().decode(dataBytes));
  } catch {
    return jsonResponse({ error: "decoded data is not valid JSON" }, 400);
  }

  // Inject transmit_time as fallback timestamp
  if (req.transmit_time && !("timestamp" in payload)) {
    // Convert Iridium format to ISO
    const iridiumRe = /^(\d{2})-(\d{2})-(\d{2})\s+(\d{2}:\d{2}:\d{2})$/;
    const m = iridiumRe.exec(req.transmit_time);
    if (m) {
      const d = new Date(`20${m[1]}-${m[2]}-${m[3]}T${m[4]}Z`);
      if (!isNaN(d.getTime())) {
        payload.timestamp = formatTime(d);
      }
    }
  }

  const sender = "iridium-" + req.imei;

  let telem: Telemetry;
  try {
    telem = parseLoRaJSON(payload, sender);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`Iridium parse error: ${msg}`);
    return jsonResponse({ error: msg }, 400);
  }

  // Override modulation
  telem.modulation = "Iridium";

  // Add Iridium-specific extra fields
  telem.iridium_latitude = req.iridium_latitude;
  telem.iridium_longitude = req.iridium_longitude;
  telem.iridium_cep = req.iridium_cep;
  telem.imei = req.imei;
  telem.momsn = req.momsn;

  console.log(`Iridium packet from IMEI ${req.imei}, callsign=${telem.payload_callsign}`);
  return forwardToSondehub(telem, env);
}

function hexDecode(hex: string): Uint8Array {
  if (hex.length % 2 !== 0) throw new Error("odd-length hex string");
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    const byte = parseInt(hex.slice(i, i + 2), 16);
    if (isNaN(byte)) throw new Error(`invalid hex at position ${i}`);
    bytes[i / 2] = byte;
  }
  return bytes;
}

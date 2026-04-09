import { type Telemetry, now, formatTime, uploadToSondehub, type ListenerPosition, uploadListenerPosition } from "./sondehub";
import { parseLoRaJSON, parseTimestamp, parseTField } from "./normalize";
import { parseAPRS } from "./aprs";
import { verifyIridiumJWT } from "./jwt";

export interface Env {
  SONDEHUB_API_URL: string;
  SOFTWARE_NAME: string;
  DEV_MODE: string;
  WORKERS_CI_COMMIT_SHA?: string;
}

function isDevMode(env: Env): boolean {
  return env.DEV_MODE === "true";
}

function commitSha(env: Env): string {
  return env.WORKERS_CI_COMMIT_SHA || "dev";
}

function jsonResponse(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function forwardToSondehub(telem: Telemetry, env: Env): Promise<Response> {
  const result = await uploadToSondehub(telem, env.SONDEHUB_API_URL, env.SOFTWARE_NAME, commitSha(env));
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
  const body = (await request.json()) as { sender?: string; raw_data?: string; timestamp?: string };
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

  // Use ground station's reception timestamp as time_received
  if (body.timestamp) {
    const parsed = parseTimestamp(body.timestamp);
    if (parsed) telem.time_received = parsed;
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
  const body = (await request.json()) as { sender?: string; raw_data?: unknown; timestamp?: string };
  if (body.raw_data === undefined || body.raw_data === null) {
    return jsonResponse({ error: "missing raw_data" }, 400);
  }

  const sender = body.sender || request.headers.get("CF-Connecting-IP") || "unknown";

  // Parse ground station's reception timestamp
  let rxTimestamp: string | undefined;
  if (body.timestamp) {
    rxTimestamp = parseTimestamp(body.timestamp);
  }

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

  // Use ground station's reception timestamp as time_received
  if (rxTimestamp) telem.time_received = rxTimestamp;

  // Parse t field (HHMM) for datetime if present
  if (typeof telem.t === "number" && rxTimestamp) {
    const refDate = new Date(rxTimestamp);
    const parsed = parseTField(telem.t, refDate);
    if (parsed) telem.datetime = parsed;
    delete telem.t;
  }

  // Fallback: datetime → time_received
  if (!telem.datetime && telem.time_received) {
    telem.datetime = telem.time_received;
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

  // Parse transmit_time for time_received
  let transmitDate: Date | undefined;
  if (req.transmit_time) {
    const parsed = parseTimestamp(req.transmit_time);
    if (parsed) {
      transmitDate = new Date(parsed);
    }
  }

  // Inject transmit_time as fallback timestamp for datetime parsing
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

  // Set time_received from Iridium transmit_time
  if (transmitDate) {
    telem.time_received = formatTime(transmitDate);
  }

  // Parse t field (HHMM) for datetime if present
  if (typeof telem.t === "number" && transmitDate) {
    const parsed = parseTField(telem.t, transmitDate);
    if (parsed) telem.datetime = parsed;
    delete telem.t;
  }

  // Fallback: datetime → time_received
  if (!telem.datetime && telem.time_received) {
    telem.datetime = telem.time_received;
  }

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

// POST /station — ground station chase vehicle position
interface StationPayload {
  callsign: string;
  lat: number;
  lon: number;
  alt: number;
  antenna?: string;
  contact_email?: string;
}

export async function handleStation(request: Request, env: Env): Promise<Response> {
  const body = (await request.json()) as StationPayload;

  if (!body.callsign || typeof body.lat !== "number" || typeof body.lon !== "number" || typeof body.alt !== "number") {
    return jsonResponse({ error: "missing required fields: callsign, lat, lon, alt" }, 400);
  }

  if (body.lat < -90 || body.lat > 90 || body.lon < -180 || body.lon > 180) {
    return jsonResponse({ error: "lat/lon out of range" }, 400);
  }

  const position: ListenerPosition = {
    software_name: env.SOFTWARE_NAME,
    software_version: commitSha(env),
    uploader_callsign: body.callsign,
    uploader_position: [body.lat, body.lon, body.alt],
    mobile: true,
  };

  if (body.antenna) position.uploader_antenna = body.antenna;
  if (body.contact_email) position.uploader_contact_email = body.contact_email;

  const result = await uploadListenerPosition(position, env.SONDEHUB_API_URL);
  if (result.status >= 200 && result.status < 300) {
    console.log(`Station position uploaded: callsign=${body.callsign}`);
    return jsonResponse({ status: "accepted" }, 202);
  }
  console.error(`Station upload failed ${result.status}: ${result.body}`);
  return jsonResponse({ status: "upstream_error", detail: result.body }, 502);
}

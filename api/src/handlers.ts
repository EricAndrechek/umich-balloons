import { type Telemetry, uploadToSondehub, type ListenerPosition, uploadListenerPosition } from "./sondehub";
import { parseLoRaJSON, parseTimestamp, resolvePayloadDatetime } from "./normalize";
import { parseAPRS } from "./aprs";
import { verifyIridiumJWT } from "./jwt";
import { decodeBody } from "./decode";
import { COMMIT_SHA } from "./version";

export interface Env {
  SONDEHUB_API_URL: string;
  SOFTWARE_NAME: string;
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
  const result = await uploadToSondehub(telem, env.SONDEHUB_API_URL, env.SOFTWARE_NAME, COMMIT_SHA);
  if (result.status === 200) {
    console.log(`SondeHub upload ok: callsign=${telem.payload_callsign}`);
    return jsonResponse({ status: "accepted" }, 202);
  }
  if (result.status > 200 && result.status < 300) {
    console.warn(`SondeHub upload accepted with issues (${result.status}): ${result.body}`);
    return jsonResponse({ status: "accepted", warning: result.body }, 202);
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
  let body: { sender?: string; raw_data?: string; timestamp?: string };
  try {
    body = (await decodeBody(request)) as typeof body;
  } catch {
    return jsonResponse({ error: "invalid request body" }, 400);
  }

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

// ---- Shared relay telemetry processing (LoRa + Iridium) ----

/**
 * Parse raw payload data and resolve timestamps.
 * Chain: balloon (t=HHMM, no seconds) → receiver (ground station or Iridium,
 * full UTC datetime) → this API worker → SondeHub.
 *
 * @param rawData  - The parsed JSON payload (object or JSON string)
 * @param receiver - Uploader callsign (ground station or "iridium-{imei}")
 * @param receiverTimestamp - UTC time from the receiver (ground station rx time or Iridium transmit_time)
 * @param modulation - "LoRa" or "Iridium"
 */
function processRelayTelemetry(
  rawData: unknown,
  receiver: string,
  receiverTimestamp: string | undefined,
  modulation: string,
): Telemetry {
  const telem = parseLoRaJSON(rawData, receiver);
  telem.modulation = modulation;

  // Use the receiver's timestamp as time_received (not our wall clock)
  let receiverDate: Date | undefined;
  if (receiverTimestamp) {
    const parsed = parseTimestamp(receiverTimestamp);
    if (parsed) {
      receiverDate = new Date(parsed);
      telem.time_received = parsed;
    }
  }

  // Resolve datetime: minutes come from the balloon's t-field (HHMM),
  // seconds come from the receiver's timestamp (best approximation).
  if (receiverDate) {
    const tField = typeof telem.t === "number" ? telem.t : undefined;
    if (tField !== undefined || !telem.datetime) {
      telem.datetime = resolvePayloadDatetime(tField, receiverDate);
    }
    console.log(`Relay datetime resolved: t=${tField}, receiver=${receiverDate.toISOString()}, datetime=${telem.datetime}`);
  }
  if ("t" in telem) delete telem.t;

  // Fallback: datetime → time_received
  if (!telem.datetime && telem.time_received) {
    telem.datetime = telem.time_received;
  }

  return telem;
}

// POST /lora — JSON with raw_data (object or JSON string)
export async function handleLoRa(request: Request, env: Env): Promise<Response> {
  let body: { sender?: string; raw_data?: unknown; timestamp?: string };
  try {
    body = (await decodeBody(request)) as typeof body;
  } catch {
    return jsonResponse({ error: "invalid request body" }, 400);
  }
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
    telem = processRelayTelemetry(rawData, sender, body.timestamp, "LoRa");
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

const IRIDIUM_REQUIRED_FIELDS = ["imei", "serial", "momsn", "transmit_time", "iridium_latitude", "iridium_longitude", "iridium_cep", "data"] as const;

export async function handleIridium(request: Request, env: Env): Promise<Response> {
  let req: IridiumPayload;
  try {
    req = (await decodeBody(request)) as IridiumPayload;
  } catch {
    return jsonResponse({ error: "invalid request body" }, 400);
  }

  // Validate required fields
  const missing = IRIDIUM_REQUIRED_FIELDS.filter((f) => req[f] === undefined || req[f] === null);
  if (missing.length > 0) {
    return jsonResponse({ error: `missing required fields: ${missing.join(", ")}` }, 400);
  }

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

  const sender = "iridium-" + req.imei;

  // Use Iridium network's transmit_time as the receiver timestamp
  let telem: Telemetry;
  try {
    telem = processRelayTelemetry(payload, sender, req.transmit_time, "Iridium");
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`Iridium parse error: ${msg}`);
    return jsonResponse({ error: msg }, 400);
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
  if (!/^[0-9a-fA-F]*$/.test(hex)) throw new Error("invalid hex characters");
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
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
  let body: StationPayload;
  try {
    body = (await decodeBody(request)) as StationPayload;
  } catch {
    return jsonResponse({ error: "invalid request body" }, 400);
  }

  if (!body.callsign || typeof body.lat !== "number" || typeof body.lon !== "number" || typeof body.alt !== "number") {
    return jsonResponse({ error: "missing required fields: callsign, lat, lon, alt" }, 400);
  }

  if (body.lat < -90 || body.lat > 90 || body.lon < -180 || body.lon > 180) {
    return jsonResponse({ error: "lat/lon out of range" }, 400);
  }

  const position: ListenerPosition = {
    software_name: env.SOFTWARE_NAME,
    software_version: COMMIT_SHA,
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

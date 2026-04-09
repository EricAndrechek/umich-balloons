import { type Telemetry, formatTime, now } from "./sondehub";

// ---- Field Aliases ----

const FIELD_ALIASES: Record<string, string[]> = {
  callsign: ["callsign", "call", "from", "payload_callsign"],
  latitude: ["latitude", "lat", "latitude_deg", "lat_deg", "lat_dd"],
  longitude: ["longitude", "lon", "longitude_deg", "lon_deg", "lon_dd"],
  altitude: ["altitude", "alt", "elevation", "elev", "height", "hgt"],
  speed: ["speed", "spd", "vel_h"],
  course: ["heading", "hdg", "course", "cse", "direction", "dir"],
  battery: ["battery_voltage", "voltage", "batt_v", "vbatt", "battery", "bat", "volt", "v", "batt"],
  sats: ["sats", "satellites", "num_sats", "gps_sats"],
  temp: ["temp", "temperature"],
  humidity: ["humidity", "hum"],
  pressure: ["pressure", "press"],
  timestamp: ["timestamp", "time", "datetime", "dt", "date_time", "data_time"],
  sender: ["sender", "uploader", "uploader_callsign"],
  frame: ["frame", "frame_number", "seq", "sequence"],
};

function resolveAlias(data: Record<string, unknown>, aliases: string[]): unknown | undefined {
  for (const alias of aliases) {
    if (alias in data) return data[alias];
    const lower = alias.toLowerCase();
    if (lower in data) return data[lower];
  }
  return undefined;
}

// Build known-key set once
const KNOWN_KEYS = new Set<string>();
for (const aliases of Object.values(FIELD_ALIASES)) {
  for (const a of aliases) {
    KNOWN_KEYS.add(a);
    KNOWN_KEYS.add(a.toLowerCase());
  }
}

// ---- Type Conversions ----

export function toFloat64(v: unknown): number | undefined {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const f = parseFloat(v);
    return isNaN(f) ? undefined : f;
  }
  return undefined;
}

export function toInt(v: unknown): number | undefined {
  if (typeof v === "number") return Math.trunc(v);
  if (typeof v === "string") {
    const n = parseInt(v, 10);
    return isNaN(n) ? undefined : n;
  }
  return undefined;
}

// ---- Coordinate Parsing ----

const DMS_RE = /^\s*(\d{1,3})[:°\s]+(\d{1,2})[:'\s]+(\d{1,2}(?:\.\d+)?)["'\s]*([NSEWnsew])?\s*$/;
const DM_RE = /^\s*(\d{1,3})[:°\s]+(\d{1,2}(?:\.\d+)?)[''\s]*([NSEWnsew])?\s*$/;
const D_RE = /^\s*(-?\d+(?:\.\d+)?)\s*([NSEWnsew])?\s*$/;

export function parseCoordinate(value: unknown, coordType: "lat" | "lon"): number {
  const maxVal = coordType === "lon" ? 180 : 90;
  const minVal = -maxVal;

  let dd: number;

  if (typeof value === "number") {
    dd = value;
    // Detect integer-scaled (e.g. 422949 = 42.2949 * 10000)
    if ((dd > maxVal || dd < minVal) && dd === Math.trunc(dd)) {
      const scaled = dd / 10000;
      if (scaled >= minVal && scaled <= maxVal) {
        dd = scaled;
      }
    }
  } else if (typeof value === "string") {
    dd = parseDMSOrDecimal(value);
  } else {
    throw new Error(`invalid type for coordinate: ${typeof value}`);
  }

  if (dd < minVal || dd > maxVal) {
    throw new Error(`coordinate ${dd.toFixed(6)} out of bounds (${minVal} to ${maxVal})`);
  }
  return dd;
}

function parseDMSOrDecimal(s: string): number {
  s = s.trim();

  // DMS: 42°17'40.2"N
  let m = DMS_RE.exec(s);
  if (m) {
    const deg = parseFloat(m[1]);
    const min = parseFloat(m[2]);
    const sec = parseFloat(m[3]);
    if (min >= 60 || sec >= 60) throw new Error(`invalid DMS (min/sec >= 60): "${s}"`);
    let dd = deg + min / 60 + sec / 3600;
    const dir = (m[4] || "").toUpperCase();
    if (dir === "S" || dir === "W") dd = -dd;
    return dd;
  }

  // DM: 42°17.67'N
  m = DM_RE.exec(s);
  if (m) {
    const deg = parseFloat(m[1]);
    const min = parseFloat(m[2]);
    if (min >= 60) throw new Error(`invalid DM (min >= 60): "${s}"`);
    let dd = deg + min / 60;
    const dir = (m[3] || "").toUpperCase();
    if (dir === "S" || dir === "W") dd = -dd;
    return dd;
  }

  // Decimal with optional direction
  m = D_RE.exec(s);
  if (m) {
    let dd = parseFloat(m[1]);
    const dir = (m[2] || "").toUpperCase();
    if (dir === "S" || dir === "W") dd = -dd;
    return dd;
  }

  // Raw float
  const dd = parseFloat(s);
  if (isNaN(dd)) throw new Error(`invalid coordinate format: "${s}"`);
  return dd;
}

// ---- Voltage Normalization ----

export function normalizeVoltage(value: unknown): number | undefined {
  const v = toFloat64(value);
  if (v === undefined || v < 0) return undefined;
  if (v > 1000) return v / 1000; // millivolts
  if (v >= 20 && v <= 60 && v === Math.trunc(v)) return v / 10; // V*10 scaled
  return v;
}

// ---- Course Normalization ----

export function normalizeCourse(v: number): number {
  v = v % 360;
  if (v < 0) v += 360;
  return v;
}

// ---- Callsign Validation ----

function isAlpha(c: string): boolean {
  return (c >= "A" && c <= "Z") || (c >= "a" && c <= "z");
}

function isAlphanumeric(s: string): boolean {
  return /^[A-Z0-9]+$/i.test(s);
}

function isNumeric(s: string): boolean {
  return /^\d+$/.test(s);
}

export function validateCallsign(callsign: string): string {
  callsign = callsign.trim().toUpperCase();
  if (!callsign) throw new Error("callsign cannot be empty");
  if (callsign.length > 9) throw new Error(`callsign "${callsign}" exceeds max length of 9`);
  if (!isAlpha(callsign[0])) throw new Error(`callsign "${callsign}" must start with a letter`);

  let base = callsign;
  let ssid = "";
  const dashIdx = callsign.indexOf("-");
  if (dashIdx >= 0) {
    base = callsign.slice(0, dashIdx);
    ssid = callsign.slice(dashIdx + 1);
  }

  if (base.length < 3 || base.length > 6) throw new Error(`base callsign "${base}" must be 3-6 chars`);
  if (!isAlphanumeric(base)) throw new Error(`base callsign "${base}" must be alphanumeric`);

  if (ssid) {
    if (ssid.length < 1 || ssid.length > 2) throw new Error(`SSID "${ssid}" must be 1-2 chars`);
    if (!isAlphanumeric(ssid)) throw new Error(`SSID "${ssid}" must be alphanumeric`);
    if (isNumeric(ssid)) {
      const n = parseInt(ssid, 10);
      if (n < 1 || n > 15) throw new Error(`numeric SSID "${ssid}" must be 1-15`);
    }
  }

  return callsign;
}

// ---- Timestamp Parsing ----

const TIMESTAMP_FORMATS = [
  // ISO variants tried via Date.parse
  undefined, // sentinel for Date.parse attempt
];

// Iridium format: "06-01-02 15:04:05" → "2006-01-02 15:04:05"
const IRIDIUM_TS_RE = /^(\d{2})-(\d{2})-(\d{2})\s+(\d{2}:\d{2}:\d{2})$/;
// Time-only: "15:04:05"
const TIME_ONLY_RE = /^(\d{2}:\d{2}:\d{2})$/;

export function parseTimestamp(v: unknown): string | undefined {
  if (typeof v === "number") {
    // Unix timestamp
    return formatTime(new Date(v > 1e12 ? v : v * 1000));
  }

  if (typeof v !== "string") return undefined;
  const s = v.trim();

  // Try standard Date.parse (handles ISO-8601, RFC2822, etc.)
  const ms = Date.parse(s);
  if (!isNaN(ms)) {
    return formatTime(new Date(ms));
  }

  // Iridium format: YY-MM-DD HH:MM:SS
  const iridium = IRIDIUM_TS_RE.exec(s);
  if (iridium) {
    const full = `20${iridium[1]}-${iridium[2]}-${iridium[3]}T${iridium[4]}Z`;
    const d = new Date(full);
    if (!isNaN(d.getTime())) return formatTime(d);
  }

  // Time-only: HH:MM:SS — use today's date
  const timeOnly = TIME_ONLY_RE.exec(s);
  if (timeOnly) {
    const today = new Date().toISOString().slice(0, 10);
    const d = new Date(`${today}T${timeOnly[1]}Z`);
    if (!isNaN(d.getTime())) return formatTime(d);
  }

  return undefined;
}

// ---- t Field (HHMM) Parsing ----

export function parseTField(t: number, referenceDate: Date): string | undefined {
  const hours = Math.floor(t / 100);
  const minutes = t % 100;
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return undefined;

  // Build datetime using reference date's year/month/day + parsed HH:MM:00
  const d = new Date(referenceDate);
  d.setUTCHours(hours, minutes, 0, 0);

  // If the computed time is after the reference, it must be the previous day
  if (d.getTime() > referenceDate.getTime()) {
    d.setUTCDate(d.getUTCDate() - 1);
  }

  return formatTime(d);
}

// ---- Main Parser ----

export function parseLoRaJSON(rawData: unknown, sender: string): Telemetry {
  let data: Record<string, unknown>;

  if (typeof rawData === "string") {
    data = JSON.parse(rawData);
  } else if (typeof rawData === "object" && rawData !== null && !Array.isArray(rawData)) {
    data = rawData as Record<string, unknown>;
  } else {
    throw new Error(`unsupported data type: ${typeof rawData}`);
  }

  return mapToTelemetry(data, sender, "LoRa");
}

function mapToTelemetry(data: Record<string, unknown>, sender: string, modulation: string): Telemetry {
  // Callsign (required)
  const csRaw = resolveAlias(data, FIELD_ALIASES.callsign);
  if (csRaw === undefined || typeof csRaw !== "string") {
    throw new Error("missing required field: callsign");
  }
  const payloadCallsign = validateCallsign(csRaw);

  // Latitude (required)
  const latRaw = resolveAlias(data, FIELD_ALIASES.latitude);
  if (latRaw === undefined) throw new Error("missing required field: latitude");
  const lat = parseCoordinate(latRaw, "lat");

  // Longitude (required)
  const lonRaw = resolveAlias(data, FIELD_ALIASES.longitude);
  if (lonRaw === undefined) throw new Error("missing required field: longitude");
  const lon = parseCoordinate(lonRaw, "lon");

  // Detect compact firmware format where lat/lon are integer-scaled (×10000).
  // When coords are scaled, altitude is encoded in hectometers (×100).
  const compactFormat =
    (typeof latRaw === "number" && (latRaw > 90 || latRaw < -90)) ||
    (typeof lonRaw === "number" && (lonRaw > 180 || lonRaw < -180));

  if (lat === 0 && lon === 0) {
    throw new Error("position 0,0 rejected (likely invalid GPS)");
  }

  const t: Telemetry = {
    software_name: "",
    software_version: "",
    uploader_callsign: sender,
    time_received: "",
    payload_callsign: payloadCallsign,
    datetime: "",
    lat,
    lon,
    alt: 0,
    modulation,
  };

  // Altitude
  const altRaw = resolveAlias(data, FIELD_ALIASES.altitude);
  if (altRaw !== undefined) {
    const alt = toFloat64(altRaw);
    if (alt !== undefined) t.alt = compactFormat ? alt * 100 : alt;
  }

  // Timestamp
  const tsRaw = resolveAlias(data, FIELD_ALIASES.timestamp);
  if (tsRaw !== undefined) {
    const ts = parseTimestamp(tsRaw);
    if (ts) t.datetime = ts;
  }
  // Leave datetime empty if no timestamp found — handlers will set fallback

  // Speed → vel_h
  const speedRaw = resolveAlias(data, FIELD_ALIASES.speed);
  if (speedRaw !== undefined) {
    const f = toFloat64(speedRaw);
    if (f !== undefined) t.vel_h = f;
  }

  // Course → heading
  const courseRaw = resolveAlias(data, FIELD_ALIASES.course);
  if (courseRaw !== undefined) {
    const f = toFloat64(courseRaw);
    if (f !== undefined) t.heading = normalizeCourse(f);
  }

  // Battery
  const battRaw = resolveAlias(data, FIELD_ALIASES.battery);
  if (battRaw !== undefined) {
    const v = normalizeVoltage(battRaw);
    if (v !== undefined) t.batt = v;
  }

  // Sats
  const satsRaw = resolveAlias(data, FIELD_ALIASES.sats);
  if (satsRaw !== undefined) {
    const n = toInt(satsRaw);
    if (n !== undefined) {
      if (n === 0) throw new Error("sats=0 rejected (likely invalid GPS)");
      t.sats = n;
    }
  }

  // Temp
  const tempRaw = resolveAlias(data, FIELD_ALIASES.temp);
  if (tempRaw !== undefined) {
    const f = toFloat64(tempRaw);
    if (f !== undefined) t.temp = f;
  }

  // Humidity
  const humRaw = resolveAlias(data, FIELD_ALIASES.humidity);
  if (humRaw !== undefined) {
    const f = toFloat64(humRaw);
    if (f !== undefined) t.humidity = f;
  }

  // Pressure
  const pressRaw = resolveAlias(data, FIELD_ALIASES.pressure);
  if (pressRaw !== undefined) {
    const f = toFloat64(pressRaw);
    if (f !== undefined) t.pressure = f;
  }

  // Frame
  const frameRaw = resolveAlias(data, FIELD_ALIASES.frame);
  if (frameRaw !== undefined) {
    const n = toInt(frameRaw);
    if (n !== undefined) t.frame = n;
  }

  // Collect extra fields
  for (const [k, v] of Object.entries(data)) {
    if (!KNOWN_KEYS.has(k) && !KNOWN_KEYS.has(k.toLowerCase())) {
      t[k] = v;
    }
  }

  return t;
}

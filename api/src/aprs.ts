import { type Telemetry, now } from "./sondehub";
import { validateCallsign, normalizeCourse } from "./normalize";

// APRS position report data type identifiers
const POSITION_TYPES = new Set([
  0x21, // '!' Position without timestamp, no messaging
  0x3d, // '=' Position without timestamp, with messaging
  0x2f, // '/' Position with timestamp, no messaging
  0x40, // '@' Position with timestamp, with messaging
]);

const ALTITUDE_RE = /\/A=(-?\d{6})/;
const SPEED_COURSE_RE = /^(\d{3})\/(\d{3})/;

export function parseAPRS(raw: string, uploaderCallsign: string): Telemetry {
  const colonIdx = raw.indexOf(":");
  if (colonIdx < 0) throw new Error("invalid APRS packet: no colon separator");

  const header = raw.slice(0, colonIdx);
  const info = raw.slice(colonIdx + 1);
  if (!info.length) throw new Error("empty APRS info field");

  // Source callsign from header
  let fromCall = header;
  const gtIdx = header.indexOf(">");
  if (gtIdx >= 0) fromCall = header.slice(0, gtIdx);
  fromCall = fromCall.trim();

  const validated = validateCallsign(fromCall);

  const dataType = info.charCodeAt(0);
  if (!POSITION_TYPES.has(dataType)) {
    throw new Error(`unsupported APRS data type: ${info[0]}`);
  }

  // Skip timestamp if present (/ or @ types)
  let body = info.slice(1);
  if (dataType === 0x2f || dataType === 0x40) {
    if (body.length < 7) throw new Error("APRS timestamp too short");
    body = body.slice(7);
  }

  let lat: number;
  let lon: number;
  let comment: string;

  if (body.length > 0 && isCompressed(body)) {
    ({ lat, lon, comment } = parseCompressedPosition(body));
  } else {
    ({ lat, lon, comment } = parseUncompressedPosition(body));
  }

  const t: Telemetry = {
    software_name: "",
    software_version: "",
    uploader_callsign: uploaderCallsign,
    time_received: "",
    payload_callsign: validated,
    datetime: now(),
    lat,
    lon,
    alt: 0,
    modulation: "APRS",
  };

  // Extract /A=NNNNNN altitude from comment
  const altMatch = ALTITUDE_RE.exec(comment);
  if (altMatch) {
    t.alt = parseFloat(altMatch[1]) * 0.3048; // feet → meters
  }

  // Extract speed/course from comment beginning
  const scMatch = SPEED_COURSE_RE.exec(comment);
  if (scMatch) {
    const course = parseFloat(scMatch[1]);
    const speedKnots = parseFloat(scMatch[2]);
    t.heading = normalizeCourse(course);
    t.vel_h = speedKnots * 0.514444; // knots → m/s
  }

  return t;
}

function isCompressed(body: string): boolean {
  if (body.length < 13) return false;
  const first = body.charCodeAt(0);
  // Uncompressed starts with digit or space for latitude
  return !(first >= 0x30 && first <= 0x39) && first !== 0x20;
}

function parseUncompressedPosition(body: string): { lat: number; lon: number; comment: string } {
  if (body.length < 18) throw new Error(`APRS position too short: ${body.length} chars`);

  const latStr = body.slice(0, 8); // DDMM.hhN
  const lonStr = body.slice(9, 18); // DDDMM.hhW
  const comment = body.length > 19 ? body.slice(19) : "";

  return {
    lat: parseAPRSLat(latStr),
    lon: parseAPRSLon(lonStr),
    comment,
  };
}

function parseAPRSLat(s: string): number {
  if (s.length < 8) throw new Error("APRS lat too short");
  const deg = parseFloat(s.slice(0, 2).replace(/ /g, "0"));
  const min = parseFloat(s.slice(2, 7).replace(/ /g, "0"));
  let dd = deg + min / 60;
  const dir = s[7];
  if (dir === "S" || dir === "s") dd = -dd;
  if (dd < -90 || dd > 90) throw new Error(`APRS lat out of range: ${dd}`);
  return dd;
}

function parseAPRSLon(s: string): number {
  if (s.length < 9) throw new Error("APRS lon too short");
  const deg = parseFloat(s.slice(0, 3).replace(/ /g, "0"));
  const min = parseFloat(s.slice(3, 8).replace(/ /g, "0"));
  let dd = deg + min / 60;
  const dir = s[8];
  if (dir === "W" || dir === "w") dd = -dd;
  if (dd < -180 || dd > 180) throw new Error(`APRS lon out of range: ${dd}`);
  return dd;
}

function parseCompressedPosition(body: string): { lat: number; lon: number; comment: string } {
  if (body.length < 13) throw new Error("compressed position too short");

  const latChars = body.slice(1, 5);
  const lonChars = body.slice(5, 9);
  const comment = body.length > 13 ? body.slice(13) : "";

  const lat = 90.0 - base91Decode(latChars) / 380926.0;
  const lon = -180.0 + base91Decode(lonChars) / 190463.0;

  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    throw new Error(`compressed coords out of range: ${lat.toFixed(6)}, ${lon.toFixed(6)}`);
  }

  return { lat, lon, comment };
}

function base91Decode(s: string): number {
  let val = 0;
  for (let i = 0; i < s.length; i++) {
    val = val * 91 + (s.charCodeAt(i) - 33);
  }
  return val;
}

// SondeHub Amateur telemetry format
export const TIME_FORMAT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;

export interface Telemetry {
  software_name: string;
  software_version: string;
  uploader_callsign: string;
  time_received: string;
  payload_callsign: string;
  datetime: string;
  lat: number;
  lon: number;
  alt: number;

  frame?: number;
  sats?: number;
  batt?: number;
  temp?: number;
  humidity?: number;
  pressure?: number;
  vel_v?: number;
  vel_h?: number;
  heading?: number;
  snr?: number;
  rssi?: number;
  frequency?: number;
  modulation?: string;
  uploader_position?: [number, number, number];
  uploader_antenna?: string;
  dev?: boolean;
  historical?: boolean;

  [extra: string]: unknown;
}

export function formatTime(d: Date): string {
  return d.toISOString().replace(/(\.\d{3})Z$/, (_, ms) => ms + "000Z");
}

export function now(): string {
  return formatTime(new Date());
}

export async function uploadToSondehub(
  telem: Telemetry,
  apiURL: string,
  softwareName: string,
  softwareVersion: string,
): Promise<{ status: number; body: string }> {
  telem.software_name = softwareName;
  telem.software_version = softwareVersion;
  if (!telem.time_received) {
    telem.time_received = now();
  }

  const payload = JSON.stringify([telem]);
  const compressed = await gzipEncode(new TextEncoder().encode(payload));

  const res = await fetchWithRetry(apiURL + "/amateur/telemetry", compressed, softwareName, softwareVersion);
  return res;
}

async function gzipEncode(data: Uint8Array): Promise<Uint8Array> {
  const cs = new CompressionStream("gzip");
  const writer = cs.writable.getWriter();
  writer.write(data);
  writer.close();

  const chunks: Uint8Array[] = [];
  const reader = cs.readable.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
  }

  let totalLen = 0;
  for (const c of chunks) totalLen += c.length;
  const result = new Uint8Array(totalLen);
  let offset = 0;
  for (const c of chunks) {
    result.set(c, offset);
    offset += c.length;
  }
  return result;
}

async function fetchWithRetry(
  url: string,
  body: Uint8Array,
  softwareName: string,
  softwareVersion: string,
  maxRetries = 2,
): Promise<{ status: number; body: string }> {
  let lastErr: string | undefined;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, 1000 * Math.pow(2, attempt - 1)));
    }

    const resp = await fetch(url, {
      method: "PUT",
      headers: {
        "User-Agent": `${softwareName}-${softwareVersion}`,
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        Date: new Date().toUTCString(),
      },
      body,
    });

    const text = await resp.text();

    if (resp.status === 200) {
      return { status: 200, body: text };
    }
    if (resp.status >= 200 && resp.status < 300) {
      return { status: resp.status, body: text };
    }
    if (resp.status >= 500) {
      lastErr = `${resp.status}: ${text}`;
      continue;
    }
    // 4xx — don't retry
    return { status: resp.status, body: text };
  }

  return { status: 500, body: `upload failed after ${maxRetries} retries: ${lastErr}` };
}

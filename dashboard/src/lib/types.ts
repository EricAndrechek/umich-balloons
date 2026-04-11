// Cloudflare Worker environment bindings
export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  SONDEHUB_API_URL: string;
  ADMIN_PASSWORD: string;
}

// D1 row types

export interface LaunchGroupRow {
  id: number;
  name: string;
  base_callsigns: string; // JSON array
  expected_balloon_count: number | null;
  active: number; // 0 or 1
  started_at: string | null;
  stopped_at: string | null;
  created_at: string;
}

export interface PayloadRow {
  id: number;
  launch_group_id: number;
  callsign: string;
  type: string;
  launched_at: string | null;
  phase: string;
  burst_altitude: number | null;
  recovered: number; // 0 or 1
  last_lat: number | null;
  last_lon: number | null;
  last_alt: number | null;
  last_heard: string | null;
  // Added in migration 0003 — cached per-payload stats maintained by the
  // cron so the dashboard doesn't need to GROUP BY over telemetry_cache
  // on every request.
  max_alt: number | null;
  total_distance_km: number | null;
  // Cursor for the cron's incremental distance calculation. Not exposed
  // to the API response, but part of the row shape because the cron
  // reads it alongside the rest of the payload fields.
  prev_track_lat: number | null;
  prev_track_lon: number | null;
  prev_track_time: string | null;
}

// Added in migration 0003.
export interface UploadersHeardRow {
  launch_group_id: number;
  uploader_callsign: string;
  best_distance_km: number | null;
  contact_count: number;
  last_contact_time: string | null;
}

export interface ContactRow {
  id: number;
  launch_group_id: number;
  balloon_callsign: string;
  uploader_callsign: string;
  modulation: string | null;
  distance_km: number | null;
  balloon_lat: number | null;
  balloon_lon: number | null;
  balloon_alt: number | null;
  uploader_lat: number | null;
  uploader_lon: number | null;
  snr: number | null;
  rssi: number | null;
  frequency: number | null;
  contact_time: string;
  sondehub_time_received: string | null;
  created_at: string;
}

export interface UploaderStatRow {
  launch_group_id: number;
  balloon_callsign: string;
  uploader_callsign: string;
  modulation: string;
  packet_count: number;
}

export interface SourceStatRow {
  launch_group_id: number;
  balloon_callsign: string;
  modulation: string;
  packet_count: number;
}

export interface TelemetryCacheRow {
  id: number;
  launch_group_id: number;
  callsign: string;
  timestamp: string;
  lat: number | null;
  lon: number | null;
  alt: number | null;
  temp: number | null;
  pressure: number | null;
  humidity: number | null;
  batt: number | null;
  sats: number | null;
  vel_v: number | null;
  vel_h: number | null;
  heading: number | null;
  snr: number | null;
  rssi: number | null;
  modulation: string | null;
  uploader_callsign: string | null;
}

export interface PredictionRow {
  launch_group_id: number;
  balloon_callsign: string;
  descending: number | null;
  landed: number | null;
  ascent_rate: number | null;
  descent_rate: number | null;
  burst_altitude: number | null;
  predicted_lat: number | null;
  predicted_lon: number | null;
  predicted_alt: number | null;
  predicted_time: string | null;
  trajectory_json: string | null;
  updated_at: string;
}

export interface CronStateRow {
  launch_group_id: number;
  callsign: string;
  last_processed_datetime: string | null;
}

// SondeHub API response types

export interface SondeHubTelemetry {
  software_name?: string;
  software_version?: string;
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
  raw?: string; // raw APRS packet (present for APRS-IS sourced records)
  comment?: string;
  [extra: string]: unknown;
}

export interface SondeHubListenerEntry {
  software_name?: string;
  software_version?: string;
  uploader_callsign: string;
  uploader_position?: [number, number, number];
  uploader_radio?: string;
  uploader_antenna?: string;
  mobile?: boolean;
  [extra: string]: unknown;
}

export interface SondeHubPrediction {
  vehicle: string;
  time: string;
  latitude: number;
  longitude: number;
  altitude: number;
  ascent_rate: number | null;
  descent_rate: number | null;
  burst_altitude: number | null;
  descending: number;
  landed: number;
  data: string; // JSON string of trajectory points
}

// API request/response types

export interface CreateLaunchGroupRequest {
  name: string;
  base_callsigns: string[];
  expected_balloon_count?: number;
}

export interface UpdateLaunchGroupRequest {
  name?: string;
  base_callsigns?: string[];
  expected_balloon_count?: number;
}

export interface LeaderboardQuery {
  balloon_callsign?: string;
  modulation?: string;
  limit?: number;
}

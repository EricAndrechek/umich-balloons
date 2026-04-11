export interface LaunchGroup {
	id: number;
	name: string;
	base_callsigns: string[];
	expected_balloon_count: number | null;
	active: boolean;
	started_at: string | null;
	stopped_at: string | null;
	created_at: string;
}

export interface Payload {
	id: number;
	launch_group_id: number;
	callsign: string;
	type: 'balloon' | 'ground_station' | 'unknown';
	launched_at: string | null;
	phase: 'pre-launch' | 'ascending' | 'descending' | 'landed';
	burst_altitude: number | null;
	recovered: number; // 0/1
	last_lat: number | null;
	last_lon: number | null;
	last_alt: number | null;
	last_heard: string | null;
	// Cached per-payload stats maintained server-side by the cron
	// (migration 0003). Authoritative across the whole flight regardless
	// of what the client currently has in its in-memory telemetry buffer,
	// so BalloonCard can show correct totals after a page refresh or
	// after the in-memory cap has dropped old points.
	max_alt: number | null;
	total_distance_km: number | null;
}

export interface LaunchGroupWithPayloads extends LaunchGroup {
	payloads: Payload[];
}

export interface Prediction {
	launch_group_id: number;
	balloon_callsign: string;
	descending: number;
	landed: number;
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

export interface Contact {
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

export interface SourceStat {
	launch_group_id: number;
	balloon_callsign: string;
	modulation: string;
	packet_count: number;
}

export interface UploaderStat {
	launch_group_id: number;
	balloon_callsign: string;
	uploader_callsign: string;
	modulation: string;
	packet_count: number;
}

export interface TelemetryCache {
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

export interface MaxAltitude {
	callsign: string;
	max_alt: number | null;
}

export interface UploaderHeard {
	uploader_callsign: string;
	best_distance_km: number | null;
	contact_count: number;
	last_contact_time: string;
}

export interface DashboardData {
	group: LaunchGroup;
	payloads: Payload[];
	predictions: Prediction[];
	sourceStats: SourceStat[];
	uploaderStats: UploaderStat[];
	latestTelemetry: TelemetryCache[];
	maxAltitudes: MaxAltitude[];
	uploadersHeard: UploaderHeard[];
}

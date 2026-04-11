export function googleMaps(lat: number, lon: number): string {
	return `https://www.google.com/maps?q=${lat},${lon}`;
}

/**
 * Build a SondeHub Amateur tracker URL that actually focuses the map on the
 * requested callsign. The tracker is an SPA: query-string `f` selects the
 * callsign before the app boots, and the `#!...` hash drives the map view
 * after boot. Both must be set — query-only leaves the map at world view,
 * hash-only sometimes loses the selection on cold loads. When we know the
 * current position, we also seed `mc` (map center) so it snaps in tight
 * instead of panning from the default.
 */
export function sondehubTracker(
	callsign: string,
	lat?: number | null,
	lon?: number | null,
): string {
	const cs = encodeURIComponent(callsign);
	const hashParts = ['mt=Mapnik', 'mz=16', 'qm=6h'];
	if (lat != null && lon != null) {
		hashParts.push(`mc=${lat},${lon}`);
	}
	hashParts.push(`f=${cs}`);
	return `https://amateur.sondehub.org/?f=${cs}#!${hashParts.join('&')}`;
}

export function aprsFi(callsign: string): string {
	return `https://aprs.fi/#!call=a/${encodeURIComponent(callsign)}`;
}

// Full `var-fields` list from the stock SondeHub Grafana "basic" dashboard.
// The dashboard defaults to a subset of fields; we pin the complete set so the
// linked view shows every telemetry variable any amateur tracker has ever
// reported. Keep this list stable — changes force a URL churn.
const GRAFANA_FIELDS = [
	'i_sys', 'reset_voltage', 'ext_temp', 'humidity', 'baudrate', 'cpu_speed',
	'pulse_counts', 'gpsofs_2_0', 'illuminance', 'button_0_0', 'SPEED (km/h)',
	'ext_pressure', 'gyro_y', 'gyro_x', 'gyro_z', 'C1', 'pyro_voltage', 'Humidity',
	'temperature_custom_1', 'temperature_custom_2', 'rssi', 'stats_1_2',
	'stats_1_1', 'stats_1_0', 'frequecy', 'meowmeow_0_0', 'clktrmcnt_2_0',
	'clktrim_0_0', 'com_temp', 'gps_0_1', 'gps_0_0', 'gps_0_3', 'gps_0_2',
	'distance_traveled', 'SPEED', 'uv_max', 'days_aloft', 'radiotrm_1_0',
	'tone_spacing', '_doc_count', 'baud_rate', 'test', 'ext_temperature', 'gps',
	'Number of Satellites', 'altura', 'daysAloft', 'solar_panel', 'Roll', 'frame',
	'rx_pkt_count', 'hr_0_0', 'gps_1_0', 'gps_1_2', 'gps_1_1', 'gps_1_3',
	'chUtil', 'radiotrm_0_0', 'Vertical Speed (ft/min)', 'internal_temp',
	'speed km/h', 'aht_humidity', 'Target lat', 'solar_voltage', 'test_0_1',
	'test_0_0', 'test_0_3', 'test_0_2', 'VDOP', 'upload_time_delta',
	'rp_xtal_code', 'clock_trim', 'system_temperature', 'jam_warning', 'rp_lu',
	'Vsol', 'power_on_time', 'sys_temperature', 'bmp_temperature', 'heading',
	'ascent_rate', 'snr', 'sats', 'noise_floor_dbm', 'pred_lat', '_size',
	'humdity', 'onboard_prediction_lat', 'lat', 'custom2_voltage',
	'custom1_voltage', 'burst_timer', 'acent_rate', 'alt', 'pressure',
	'uploader_alt', 'acc_y', 'acc_x', 'soc_temperature', 'TTF', 'focus_fom',
	'gas_0_0', 'commands_succeeded', 'flight_number', 'ttf', 'cpu_temp',
	'uptime', 'speed_0_0', 'speed m/s', 'gas_resistance', 'cur_1_0', 'Demo',
	'sensor_temp', 'cur_0_0', 'CPU temperature', 'commands_failed', 'my_field_3',
	'payload_voltage', 'my_field_1', 'Yaw', 'bmp_altitude', 'temperature',
	'uv_avg', 'time_to_fix', 'rot_x', 'rot_y', 'lora_speed', 'solar_elevation',
	'radio_temp', 'external_temperature', 'frequency', 'Memorial Flight',
	'fix_voltage', 'heat_1_1', 'ext_humidity', 'heat_1_0',
	'ext_temperature_custom_2', 'ext_temperature_custom_1', 'speed', 'batt',
	'aht_temperature', 'lens_position', 'prefix1', 'prefix2', 'vel_h',
	'gps_restarts', 'current_mA', 'PDOP', 'commands_received', 'vel_v',
	'AVG_SNR', 'SOLAR PANELS (V)', 'temps-heat_3_1', 'temps-heat_3_2',
	'temps-heat_3_3', 'millis_1_0', 'temps-heat_3_0', 'cal_1_3', 'cal_1_0',
	'ibatt', 'cal_1_1', 'cal_1_2', 'accel_y', 'accel_z', 'accel_x', 'RX count',
	'meowmeow_3_0', 'pred_lon', 'disk_percent', 'Heading', 'sonde_type', 'flags',
	'solar', 'Groundspeed (knots)', 'io_voltage', 'lon', 'Baro Altitude [m]',
	'vcc', 'power', 'cal_0_0', 'cal_0_1', 'cal_0_2', 'cal_0_3', 'alt_baro',
	'bmp_pressure', 'mcu_temp', 'airUtilTx', 'solar_panel_voltage',
	'device_status', 'tx_frequency', 'aux_temp', 'stats_2_2', 'stats_2_1',
	'stats_2_0', 'Pitch', 'meowmeow_1_0', 'debug_0_0', 'temps-heat_2_2',
	'pv_voltage', 'temps-heat_2_3', 'temps-heat_2_0', 'temps-heat_2_1',
	'usofs_1_0', 'num. satélites', 'batt_i', 'temp', 'TEMPERATURE (°C)',
	'Target lon', 'radiation_intensity', 'days aloft', 'load_avg_15',
	'load_avg_1', 'meowmeow_2_0', 'HDOP', 'load_avg_5', 'mcu_calibration',
	'batt_v',
];

/**
 * Build a SondeHub Grafana link pinned to a specific payload and time range.
 * `fromIso` / `toIso` should be ISO-8601 strings (the Grafana "basic"
 * dashboard parses both absolute timestamps and relative ranges here).
 */
export function grafanaBalloon(callsign: string, fromIso: string, toIso: string): string {
	const parts: string[] = [
		`var-Payload=${encodeURIComponent(callsign)}`,
		`from=${encodeURIComponent(fromIso)}`,
		`to=${encodeURIComponent(toIso)}`,
		'orgId=1',
		'timezone=utc',
	];
	for (const f of GRAFANA_FIELDS) parts.push(`var-fields=${encodeURIComponent(f)}`);
	parts.push('refresh=1m');
	return `https://grafana.v2.sondehub.org/d/HJgOZLq7k/basic?${parts.join('&')}`;
}

export const RESOURCE_LINKS = [
	{ label: 'SondeHub Amateur', url: 'https://amateur.sondehub.org/', desc: 'Live balloon tracking' },
	{ label: 'SondeHub Predictor', url: 'https://predict.sondehub.org/', desc: 'Flight path prediction' },
	{ label: 'Burst Calculator', url: 'https://sondehub.org/calc/', desc: 'Balloon burst altitude' },
	{ label: 'aprs.fi', url: 'https://aprs.fi/', desc: 'APRS tracking network' },
	{ label: 'SondeHub Grafana', url: 'https://grafana.v2.sondehub.org/', desc: 'Telemetry dashboards' },
	{ label: 'GitHub', url: 'https://github.com/umich-balloons', desc: 'Project repositories' },
];

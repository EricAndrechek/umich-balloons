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

export const RESOURCE_LINKS = [
	{ label: 'SondeHub Amateur', url: 'https://amateur.sondehub.org/', desc: 'Live balloon tracking' },
	{ label: 'SondeHub Predictor', url: 'https://predict.sondehub.org/', desc: 'Flight path prediction' },
	{ label: 'Burst Calculator', url: 'https://kaymont.com/burst-calculator', desc: 'Balloon burst altitude' },
	{ label: 'aprs.fi', url: 'https://aprs.fi/', desc: 'APRS tracking network' },
	{ label: 'SondeHub Grafana', url: 'https://grafana.v2.sondehub.org/', desc: 'Telemetry dashboards' },
	{ label: 'GitHub', url: 'https://github.com/umich-balloons', desc: 'Project repositories' },
];

-- Launch groups (configs)
CREATE TABLE launch_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  base_callsigns TEXT NOT NULL,
  expected_balloon_count INTEGER,
  active INTEGER DEFAULT 0,
  started_at TEXT,
  stopped_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Discovered payloads within a launch group
CREATE TABLE payloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  launch_group_id INTEGER NOT NULL REFERENCES launch_groups(id) ON DELETE CASCADE,
  callsign TEXT NOT NULL,
  type TEXT DEFAULT 'unknown',
  launched_at TEXT,
  phase TEXT DEFAULT 'pre-launch',
  burst_altitude REAL,
  recovered INTEGER DEFAULT 0,
  last_lat REAL,
  last_lon REAL,
  last_alt REAL,
  last_heard TEXT,
  UNIQUE(launch_group_id, callsign)
);

-- Every contact record (for leaderboard with full detail)
CREATE TABLE contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  launch_group_id INTEGER NOT NULL,
  balloon_callsign TEXT NOT NULL,
  uploader_callsign TEXT NOT NULL,
  modulation TEXT,
  distance_km REAL,
  balloon_lat REAL,
  balloon_lon REAL,
  balloon_alt REAL,
  uploader_lat REAL,
  uploader_lon REAL,
  snr REAL,
  rssi REAL,
  frequency REAL,
  contact_time TEXT NOT NULL,
  sondehub_time_received TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_contacts_leaderboard
  ON contacts(launch_group_id, balloon_callsign, modulation, distance_km DESC);
CREATE INDEX idx_contacts_uploader
  ON contacts(launch_group_id, uploader_callsign);
CREATE INDEX idx_contacts_time
  ON contacts(launch_group_id, balloon_callsign, contact_time);

-- Aggregated uploader stats
CREATE TABLE uploader_stats (
  launch_group_id INTEGER NOT NULL,
  balloon_callsign TEXT NOT NULL,
  uploader_callsign TEXT NOT NULL,
  modulation TEXT NOT NULL,
  packet_count INTEGER DEFAULT 0,
  PRIMARY KEY (launch_group_id, balloon_callsign, uploader_callsign, modulation)
);

-- Source breakdown
CREATE TABLE source_stats (
  launch_group_id INTEGER NOT NULL,
  balloon_callsign TEXT NOT NULL,
  modulation TEXT NOT NULL,
  packet_count INTEGER DEFAULT 0,
  PRIMARY KEY (launch_group_id, balloon_callsign, modulation)
);

-- Recent telemetry cache (for sparklines)
CREATE TABLE telemetry_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  launch_group_id INTEGER NOT NULL,
  callsign TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  lat REAL,
  lon REAL,
  alt REAL,
  temp REAL,
  pressure REAL,
  humidity REAL,
  batt REAL,
  sats INTEGER,
  vel_v REAL,
  vel_h REAL,
  heading REAL,
  snr REAL,
  rssi REAL,
  modulation TEXT,
  uploader_callsign TEXT
);

CREATE INDEX idx_telemetry_cache_lookup
  ON telemetry_cache(launch_group_id, callsign, timestamp);

-- Prediction cache (from SondeHub /amateur/predictions)
CREATE TABLE predictions (
  launch_group_id INTEGER NOT NULL,
  balloon_callsign TEXT NOT NULL,
  descending INTEGER,
  landed INTEGER,
  ascent_rate REAL,
  descent_rate REAL,
  burst_altitude REAL,
  predicted_lat REAL,
  predicted_lon REAL,
  predicted_alt REAL,
  predicted_time TEXT,
  trajectory_json TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (launch_group_id, balloon_callsign)
);

-- Cron processing state
CREATE TABLE cron_state (
  launch_group_id INTEGER NOT NULL,
  callsign TEXT NOT NULL,
  last_processed_datetime TEXT,
  PRIMARY KEY (launch_group_id, callsign)
);

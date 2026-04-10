// Package types defines shared data types used across umbgs subsystems.
package types

import "time"

// Packet represents a telemetry packet received from any source.
type Packet struct {
	Source   string                 `json:"source"`             // "aprs" or "lora"
	RawData  string                 `json:"raw_data"`           // Original raw data string
	Parsed   map[string]interface{} `json:"parsed,omitempty"`   // Parsed JSON fields (LoRa), nil for APRS
	Endpoint string                 `json:"endpoint"`           // API endpoint to upload to ("/aprs" or "/lora")
	Sender   string                 `json:"sender"`             // Uploader callsign (e.g., "KD8CJT-9")
	Time     time.Time              `json:"time"`               // When the packet was received locally
}

// Position represents a GPS fix from gpsd.
type Position struct {
	Lat  float64
	Lon  float64
	Alt  float64
	Time time.Time
}

// PacketStatus indicates the upload outcome.
type PacketStatus int

const (
	StatusUploaded PacketStatus = iota
	StatusFailed
	StatusBuffered
)

// PacketEvent is emitted by the uploader for the dashboard.
type PacketEvent struct {
	Packet    Packet       `json:"packet"`
	Status    PacketStatus `json:"status"`
	Error     string       `json:"error,omitempty"`
	Timestamp time.Time    `json:"timestamp"`
}

// LogEntry represents a structured log line for the dashboard.
type LogEntry struct {
	Timestamp time.Time         `json:"timestamp"`
	Service   string            `json:"service"` // "umbgs", "direwolf", "gpsd", "system"
	Level     string            `json:"level"`   // "DEBUG", "INFO", "WARN", "ERROR"
	Message   string            `json:"message"`
	Fields    map[string]string `json:"fields,omitempty"`
}

// SystemStats holds system metrics for the dashboard.
type SystemStats struct {
	CPUPercent  float64           `json:"cpu_percent"`
	RAMPercent  float64           `json:"ram_percent"`
	RAMUsedMB   float64           `json:"ram_used_mb"`
	RAMTotalMB  float64           `json:"ram_total_mb"`
	CPUTempC    float64           `json:"cpu_temp_c"`
	Uptime      int64             `json:"uptime_seconds"`
	Version     string            `json:"version"`
	Online      bool              `json:"online"`
	BufferDepth int               `json:"buffer_depth"`
	FailedCount int               `json:"failed_count"`
	GPSFix      bool              `json:"gps_fix"`
	GPSLat      float64           `json:"gps_lat"`
	GPSLon      float64           `json:"gps_lon"`
	GPSAlt      float64           `json:"gps_alt"`
	Services    map[string]string `json:"services"`
	Network     NetworkStatus     `json:"network"`
	LEDState    string            `json:"led_state"`
	Timestamp   time.Time         `json:"timestamp"`
}

// NetworkStatus describes current network connectivity.
type NetworkStatus struct {
	Connectivity string         `json:"connectivity"` // "full", "limited", "none", "portal"
	Interfaces   []NetInterface `json:"interfaces"`
	APMode       bool           `json:"ap_mode"`
	APSSID       string         `json:"ap_ssid,omitempty"`
}

// NetInterface describes a network interface.
type NetInterface struct {
	Name   string `json:"name"`
	Type   string `json:"type"` // "ethernet", "wifi"
	IP     string `json:"ip"`
	SSID   string `json:"ssid,omitempty"`
	Signal int    `json:"signal,omitempty"` // WiFi signal strength 0-100
	State  string `json:"state"`            // "connected", "disconnected"
}

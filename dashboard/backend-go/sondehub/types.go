package sondehub

import "time"

// TimeFormat is the ISO-8601 format expected by SondeHub.
const TimeFormat = "2006-01-02T15:04:05.000000Z"

// Telemetry represents the SondeHub Amateur telemetry format.
// See: https://github.com/projecthorus/sondehub-infra/wiki/API-(Beta)#amateur_telemetry_format
type Telemetry struct {
	// Required fields
	SoftwareName     string  `json:"software_name"`
	SoftwareVersion  string  `json:"software_version"`
	UploaderCallsign string  `json:"uploader_callsign"`
	TimeReceived     string  `json:"time_received"`
	PayloadCallsign  string  `json:"payload_callsign"`
	Datetime         string  `json:"datetime"`
	Lat              float64 `json:"lat"`
	Lon              float64 `json:"lon"`
	Alt              float64 `json:"alt"`

	// Optional telemetry fields
	Frame    *int     `json:"frame,omitempty"`
	Sats     *int     `json:"sats,omitempty"`
	Batt     *float64 `json:"batt,omitempty"`
	Temp     *float64 `json:"temp,omitempty"`
	Humidity *float64 `json:"humidity,omitempty"`
	Pressure *float64 `json:"pressure,omitempty"`
	VelV     *float64 `json:"vel_v,omitempty"`
	VelH     *float64 `json:"vel_h,omitempty"`
	Heading  *float64 `json:"heading,omitempty"`

	// Optional listener metadata
	SNR              *float64    `json:"snr,omitempty"`
	RSSI             *float64    `json:"rssi,omitempty"`
	Frequency        *float64    `json:"frequency,omitempty"`
	Modulation       *string     `json:"modulation,omitempty"`
	UploaderPosition *[3]float64 `json:"uploader_position,omitempty"`
	UploaderAntenna  *string     `json:"uploader_antenna,omitempty"`

	// Dev mode flag - if true, SondeHub accepts but discards the data
	Dev *bool `json:"dev,omitempty"`
	// Historical flag - set true if uploading past data
	Historical *bool `json:"historical,omitempty"`

	// Extra custom fields
	ExtraFields map[string]interface{} `json:"-"`
}

// FormatTime formats a time.Time to SondeHub's expected ISO-8601 format.
func FormatTime(t time.Time) string {
	return t.UTC().Format(TimeFormat)
}

// Now returns the current time formatted for SondeHub.
func Now() string {
	return FormatTime(time.Now())
}

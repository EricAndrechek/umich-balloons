package normalize

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/sondehub"
)

// FieldAliases maps canonical field names to their accepted aliases.
// Mirrors the Python ParsedPacket's AliasChoices.
var FieldAliases = map[string][]string{
	"callsign":  {"callsign", "call", "from", "payload_callsign"},
	"latitude":  {"latitude", "lat", "latitude_deg", "lat_deg", "lat_dd"},
	"longitude": {"longitude", "lon", "longitude_deg", "lon_deg", "lon_dd"},
	"altitude":  {"altitude", "alt", "elevation", "elev", "height", "hgt"},
	"speed":     {"speed", "spd", "vel_h"},
	"course":    {"heading", "hdg", "course", "cse", "direction", "dir"},
	"battery":   {"battery_voltage", "voltage", "batt_v", "vbatt", "battery", "bat", "volt", "v", "batt"},
	"sats":      {"sats", "satellites", "num_sats", "gps_sats"},
	"temp":      {"temp", "temperature"},
	"humidity":  {"humidity", "hum"},
	"pressure":  {"pressure", "press"},
	"timestamp": {"timestamp", "time", "datetime", "dt", "date_time", "data_time"},
	"sender":    {"sender", "uploader", "uploader_callsign"},
	"frame":     {"frame", "frame_number", "seq", "sequence"},
}

// resolveAlias looks up a value from a map using alias fallback.
func resolveAlias(data map[string]interface{}, aliases []string) (interface{}, bool) {
	for _, alias := range aliases {
		if v, ok := data[alias]; ok {
			return v, true
		}
		// Also try lowercase version
		lower := strings.ToLower(alias)
		if v, ok := data[lower]; ok {
			return v, true
		}
	}
	return nil, false
}

// ParseLoRaJSON parses a LoRa JSON payload into a SondeHub Telemetry object.
func ParseLoRaJSON(rawData interface{}, sender string) (*sondehub.Telemetry, error) {
	var data map[string]interface{}

	switch v := rawData.(type) {
	case map[string]interface{}:
		data = v
	case string:
		if err := json.Unmarshal([]byte(v), &data); err != nil {
			return nil, fmt.Errorf("invalid JSON: %w", err)
		}
	case []byte:
		if err := json.Unmarshal(v, &data); err != nil {
			return nil, fmt.Errorf("invalid JSON: %w", err)
		}
	default:
		return nil, fmt.Errorf("unsupported data type: %T", rawData)
	}

	return mapToTelemetry(data, sender, "LoRa")
}

// mapToTelemetry converts a generic key-value map to a SondeHub Telemetry struct.
func mapToTelemetry(data map[string]interface{}, sender, modulation string) (*sondehub.Telemetry, error) {
	t := &sondehub.Telemetry{
		UploaderCallsign: sender,
	}

	// Callsign (required)
	if v, ok := resolveAlias(data, FieldAliases["callsign"]); ok {
		cs, ok := v.(string)
		if !ok {
			return nil, fmt.Errorf("callsign must be a string")
		}
		validated, err := ValidateCallsign(cs)
		if err != nil {
			return nil, fmt.Errorf("invalid callsign: %w", err)
		}
		t.PayloadCallsign = validated
	} else {
		return nil, fmt.Errorf("missing required field: callsign")
	}

	// Latitude (required)
	if v, ok := resolveAlias(data, FieldAliases["latitude"]); ok {
		lat, err := ParseCoordinate(v, "lat")
		if err != nil {
			return nil, fmt.Errorf("invalid latitude: %w", err)
		}
		t.Lat = lat
	} else {
		return nil, fmt.Errorf("missing required field: latitude")
	}

	// Longitude (required)
	if v, ok := resolveAlias(data, FieldAliases["longitude"]); ok {
		lon, err := ParseCoordinate(v, "lon")
		if err != nil {
			return nil, fmt.Errorf("invalid longitude: %w", err)
		}
		t.Lon = lon
	} else {
		return nil, fmt.Errorf("missing required field: longitude")
	}

	// Reject 0,0 positions
	if t.Lat == 0 && t.Lon == 0 {
		return nil, fmt.Errorf("position 0,0 rejected (likely invalid GPS)")
	}

	// Altitude (optional but important)
	if v, ok := resolveAlias(data, FieldAliases["altitude"]); ok {
		alt, err := toFloat64(v)
		if err == nil {
			t.Alt = alt
		}
	}

	// Timestamp
	if v, ok := resolveAlias(data, FieldAliases["timestamp"]); ok {
		if ts, err := parseTimestamp(v); err == nil {
			t.Datetime = sondehub.FormatTime(ts)
		}
	}
	if t.Datetime == "" {
		t.Datetime = sondehub.Now()
	}

	// Speed -> vel_h
	if v, ok := resolveAlias(data, FieldAliases["speed"]); ok {
		if f, err := toFloat64(v); err == nil {
			t.VelH = &f
		}
	}

	// Course -> heading
	if v, ok := resolveAlias(data, FieldAliases["course"]); ok {
		if f, err := toFloat64(v); err == nil {
			h := NormalizeCourse(f)
			t.Heading = &h
		}
	}

	// Battery
	if v, ok := resolveAlias(data, FieldAliases["battery"]); ok {
		if volts, valid := NormalizeVoltage(v); valid {
			t.Batt = &volts
		}
	}

	// Sats
	if v, ok := resolveAlias(data, FieldAliases["sats"]); ok {
		if n, err := toInt(v); err == nil {
			if n == 0 {
				return nil, fmt.Errorf("sats=0 rejected (likely invalid GPS)")
			}
			t.Sats = &n
		}
	}

	// Temp
	if v, ok := resolveAlias(data, FieldAliases["temp"]); ok {
		if f, err := toFloat64(v); err == nil {
			t.Temp = &f
		}
	}

	// Humidity
	if v, ok := resolveAlias(data, FieldAliases["humidity"]); ok {
		if f, err := toFloat64(v); err == nil {
			t.Humidity = &f
		}
	}

	// Pressure
	if v, ok := resolveAlias(data, FieldAliases["pressure"]); ok {
		if f, err := toFloat64(v); err == nil {
			t.Pressure = &f
		}
	}

	// Frame
	if v, ok := resolveAlias(data, FieldAliases["frame"]); ok {
		if n, err := toInt(v); err == nil {
			t.Frame = &n
		}
	}

	// Modulation
	mod := modulation
	t.Modulation = &mod

	// Collect extra fields (anything not in our alias map)
	knownKeys := make(map[string]bool)
	for _, aliases := range FieldAliases {
		for _, a := range aliases {
			knownKeys[a] = true
			knownKeys[strings.ToLower(a)] = true
		}
	}
	extras := make(map[string]interface{})
	for k, v := range data {
		if !knownKeys[k] && !knownKeys[strings.ToLower(k)] {
			extras[k] = v
		}
	}
	if len(extras) > 0 {
		t.ExtraFields = extras
	}

	return t, nil
}

func toFloat64(v interface{}) (float64, error) {
	switch val := v.(type) {
	case float64:
		return val, nil
	case int:
		return float64(val), nil
	case int64:
		return float64(val), nil
	case json.Number:
		return val.Float64()
	case string:
		f, err := parseFloat(val)
		if err != nil {
			return 0, err
		}
		return f, nil
	default:
		return 0, fmt.Errorf("cannot convert %T to float64", v)
	}
}

func parseFloat(s string) (float64, error) {
	s = strings.TrimSpace(s)
	var f float64
	_, err := fmt.Sscanf(s, "%f", &f)
	return f, err
}

func toInt(v interface{}) (int, error) {
	switch val := v.(type) {
	case float64:
		return int(val), nil
	case int:
		return val, nil
	case int64:
		return int(val), nil
	case json.Number:
		i, err := val.Int64()
		return int(i), err
	case string:
		var i int
		_, err := fmt.Sscanf(val, "%d", &i)
		return i, err
	default:
		return 0, fmt.Errorf("cannot convert %T to int", v)
	}
}

func parseTimestamp(v interface{}) (time.Time, error) {
	switch val := v.(type) {
	case string:
		// Try multiple formats
		formats := []string{
			time.RFC3339Nano,
			time.RFC3339,
			"2006-01-02T15:04:05Z",
			"2006-01-02T15:04:05.000000Z",
			"2006-01-02 15:04:05",
			"06-01-02 15:04:05", // Iridium format
			"15:04:05",          // Time only
		}
		for _, f := range formats {
			if t, err := time.Parse(f, val); err == nil {
				// For time-only, add today's date
				if f == "15:04:05" {
					now := time.Now().UTC()
					t = time.Date(now.Year(), now.Month(), now.Day(), t.Hour(), t.Minute(), t.Second(), 0, time.UTC)
				}
				return t.UTC(), nil
			}
		}
		return time.Time{}, fmt.Errorf("could not parse timestamp: %q", val)
	case float64:
		return time.Unix(int64(val), 0).UTC(), nil
	case int64:
		return time.Unix(val, 0).UTC(), nil
	default:
		return time.Time{}, fmt.Errorf("unsupported timestamp type: %T", v)
	}
}

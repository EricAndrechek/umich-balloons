package normalize

import (
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

var dmsPattern = regexp.MustCompile(`(?i)^\s*(\d{1,3})[:°\s]+(\d{1,2})[:'\s]+(\d{1,2}(?:\.\d+)?)["'\s]*([NSEWnsew])?\s*$`)
var dmPattern = regexp.MustCompile(`(?i)^\s*(\d{1,3})[:°\s]+(\d{1,2}(?:\.\d+)?)[''\s]*([NSEWnsew])?\s*$`)
var dPattern = regexp.MustCompile(`(?i)^\s*(-?\d+(?:\.\d+)?)\s*([NSEWnsew])?\s*$`)

// ParseCoordinate parses a coordinate value from various formats into decimal degrees.
// coordType must be "lat" or "lon".
// Accepts: float64, int (assumed *10000), or string (DMS or decimal).
func ParseCoordinate(value interface{}, coordType string) (float64, error) {
	maxVal := 90.0
	if coordType == "lon" {
		maxVal = 180.0
	}
	minVal := -maxVal

	var dd float64
	switch v := value.(type) {
	case float64:
		dd = v
		// Detect integer-scaled coordinates (e.g. 422949 = 42.2949 * 10000)
		// JSON unmarshal produces float64 for all numbers, so we can't rely on Go int type
		if (dd > maxVal || dd < minVal) && dd == math.Trunc(dd) {
			scaled := dd / 10000.0
			if scaled >= minVal && scaled <= maxVal {
				dd = scaled
			}
		}
	case int:
		dd = float64(v) / 10000.0
	case int64:
		dd = float64(v) / 10000.0
	case string:
		parsed, err := parseDMSOrDecimal(v, coordType)
		if err != nil {
			return 0, err
		}
		dd = parsed
	default:
		return 0, fmt.Errorf("invalid type for coordinate: %T", value)
	}

	if dd < minVal || dd > maxVal {
		return 0, fmt.Errorf("coordinate %.6f out of bounds (%.0f to %.0f)", dd, minVal, maxVal)
	}
	return dd, nil
}

func parseDMSOrDecimal(s string, coordType string) (float64, error) {
	s = strings.TrimSpace(s)

	// Try DMS: 42°17'40.2"N
	if m := dmsPattern.FindStringSubmatch(s); m != nil {
		deg, _ := strconv.ParseFloat(m[1], 64)
		min, _ := strconv.ParseFloat(m[2], 64)
		sec, _ := strconv.ParseFloat(m[3], 64)
		if min >= 60 || sec >= 60 {
			return 0, fmt.Errorf("invalid DMS values (min/sec >= 60): %q", s)
		}
		dd := deg + min/60.0 + sec/3600.0
		if dir := strings.ToUpper(m[4]); dir == "S" || dir == "W" {
			dd = -dd
		}
		return dd, nil
	}

	// Try DM: 42°17.67'N
	if m := dmPattern.FindStringSubmatch(s); m != nil {
		deg, _ := strconv.ParseFloat(m[1], 64)
		min, _ := strconv.ParseFloat(m[2], 64)
		if min >= 60 {
			return 0, fmt.Errorf("invalid DM values (min >= 60): %q", s)
		}
		dd := deg + min/60.0
		if dir := strings.ToUpper(m[3]); dir == "S" || dir == "W" {
			dd = -dd
		}
		return dd, nil
	}

	// Try plain decimal (with optional direction)
	if m := dPattern.FindStringSubmatch(s); m != nil {
		dd, err := strconv.ParseFloat(m[1], 64)
		if err != nil {
			return 0, fmt.Errorf("invalid decimal coordinate: %q", s)
		}
		if dir := strings.ToUpper(m[2]); dir == "S" || dir == "W" {
			dd = -dd
		}
		return dd, nil
	}

	// Last try: raw float parse
	dd, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid coordinate format: %q", s)
	}
	return dd, nil
}

// NormalizeVoltage normalizes battery voltage to Volts.
// Handles millivolts (>1000), scaled V*10 (int 20-60), and direct volts.
func NormalizeVoltage(value interface{}) (float64, bool) {
	var v float64
	isInt := false
	switch val := value.(type) {
	case float64:
		v = val
	case int:
		v = float64(val)
		isInt = true
	case int64:
		v = float64(val)
		isInt = true
	case nil:
		return 0, false
	default:
		return 0, false
	}
	if v < 0 {
		return 0, false
	}
	if v > 1000 {
		return v / 1000.0, true
	}
	// Detect V*10 scaling: integer-like values 20-60 (i.e. 2.0V-6.0V)
	// JSON unmarshal always produces float64, so also check float64 with no fractional part
	if v >= 20 && v <= 60 && (isInt || v == math.Trunc(v)) {
		return v / 10.0, true
	}
	return v, true
}

// NormalizeCourse normalizes a course/heading value to 0-360 degrees.
func NormalizeCourse(v float64) float64 {
	v = math.Mod(v, 360)
	if v < 0 {
		v += 360
	}
	return v
}

// ValidateCallsign validates and normalizes an APRS-style callsign.
func ValidateCallsign(callsign string) (string, error) {
	callsign = strings.ToUpper(strings.TrimSpace(callsign))
	if callsign == "" {
		return "", fmt.Errorf("callsign cannot be empty")
	}
	if len(callsign) > 9 {
		return "", fmt.Errorf("callsign %q exceeds max length of 9", callsign)
	}
	if !isAlpha(rune(callsign[0])) {
		return "", fmt.Errorf("callsign %q must start with a letter", callsign)
	}
	base := callsign
	ssid := ""
	if idx := strings.Index(callsign, "-"); idx >= 0 {
		base = callsign[:idx]
		ssid = callsign[idx+1:]
	}
	if len(base) < 3 || len(base) > 6 {
		return "", fmt.Errorf("base callsign %q must be 3-6 chars", base)
	}
	if !isAlphanumeric(base) {
		return "", fmt.Errorf("base callsign %q must be alphanumeric", base)
	}
	if ssid != "" {
		if len(ssid) < 1 || len(ssid) > 2 {
			return "", fmt.Errorf("SSID %q must be 1-2 chars", ssid)
		}
		if !isAlphanumeric(ssid) {
			return "", fmt.Errorf("SSID %q must be alphanumeric", ssid)
		}
		if isNumeric(ssid) {
			n, _ := strconv.Atoi(ssid)
			if n < 1 || n > 15 {
				return "", fmt.Errorf("numeric SSID %q must be 1-15", ssid)
			}
		}
	}
	return callsign, nil
}

func isAlpha(r rune) bool {
	return (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z')
}

func isAlphanumeric(s string) bool {
	for _, r := range s {
		if !((r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9')) {
			return false
		}
	}
	return true
}

func isNumeric(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

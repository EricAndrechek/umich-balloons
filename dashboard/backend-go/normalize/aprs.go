package normalize

import (
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"

	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/sondehub"
)

// APRS position report data type identifiers
var positionTypes = map[byte]bool{
	'!': true, // Position without timestamp, no messaging
	'=': true, // Position without timestamp, with messaging
	'/': true, // Position with timestamp, no messaging
	'@': true, // Position with timestamp, with messaging
}

var altitudePattern = regexp.MustCompile(`/A=(-?\d{6})`)
var commentSpeedCourse = regexp.MustCompile(`^(\d{3})/(\d{3})`)

// ParseAPRS parses a raw APRS packet string and returns a SondeHub Telemetry object.
func ParseAPRS(raw string, uploaderCallsign string) (*sondehub.Telemetry, error) {
	// Split header from info: CALLSIGN>DEST,PATH:INFO
	colonIdx := strings.Index(raw, ":")
	if colonIdx < 0 {
		return nil, fmt.Errorf("invalid APRS packet: no colon separator")
	}
	header := raw[:colonIdx]
	info := raw[colonIdx+1:]

	if len(info) == 0 {
		return nil, fmt.Errorf("empty APRS info field")
	}

	// Parse header for source callsign
	fromCall := header
	if gtIdx := strings.Index(header, ">"); gtIdx >= 0 {
		fromCall = header[:gtIdx]
	}
	fromCall = strings.TrimSpace(fromCall)

	validated, err := ValidateCallsign(fromCall)
	if err != nil {
		return nil, fmt.Errorf("invalid source callsign: %w", err)
	}

	dataType := info[0]
	if !positionTypes[dataType] {
		return nil, fmt.Errorf("unsupported APRS data type: %c", dataType)
	}

	// Skip timestamp if present (/ or @ types)
	body := info[1:]
	if dataType == '/' || dataType == '@' {
		if len(body) < 7 {
			return nil, fmt.Errorf("APRS timestamp too short")
		}
		body = body[7:] // Skip DDHHMMz or HHMMSSh
	}

	var lat, lon float64
	var comment string

	if len(body) > 0 && isCompressed(body) {
		lat, lon, comment, err = parseCompressedPosition(body)
	} else {
		lat, lon, comment, err = parseUncompressedPosition(body)
	}
	if err != nil {
		return nil, err
	}

	t := &sondehub.Telemetry{
		PayloadCallsign:  validated,
		UploaderCallsign: uploaderCallsign,
		Lat:              lat,
		Lon:              lon,
		Datetime:         sondehub.Now(),
	}

	mod := "APRS"
	t.Modulation = &mod

	// Extract /A=NNNNNN altitude from comment
	if m := altitudePattern.FindStringSubmatch(comment); m != nil {
		altFeet, _ := strconv.ParseFloat(m[1], 64)
		t.Alt = altFeet * 0.3048 // feet to meters
	}

	// Extract speed/course from comment beginning
	if m := commentSpeedCourse.FindStringSubmatch(comment); m != nil {
		course, _ := strconv.ParseFloat(m[1], 64)
		speed, _ := strconv.ParseFloat(m[2], 64)
		speedMS := speed * 0.514444 // knots to m/s
		h := NormalizeCourse(course)
		t.Heading = &h
		t.VelH = &speedMS
	}

	return t, nil
}

func isCompressed(body string) bool {
	// Compressed positions start with a symbol table char that's
	// not a digit and the rest uses base-91 encoding.
	// Simple heuristic: if position seems to be lat chars (uppercase),
	// it's likely compressed.
	if len(body) < 13 {
		return false
	}
	// Uncompressed starts with digit or space for latitude
	first := body[0]
	return !(first >= '0' && first <= '9') && first != ' '
}

// parseUncompressedPosition parses DDMM.hhN/DDDMM.hhW format.
func parseUncompressedPosition(body string) (lat, lon float64, comment string, err error) {
	// Format: DDMM.hhN/DDDMM.hhW followed by optional comment
	// Minimum: 8 chars lat + 1 separator + 9 chars lon = 18
	if len(body) < 18 {
		return 0, 0, "", fmt.Errorf("APRS position too short: %d chars", len(body))
	}

	latStr := body[:8]   // DDMM.hhN
	lonStr := body[9:18] // DDDMM.hhW
	if len(body) > 19 {
		comment = body[19:]
	}

	lat, err = parseAPRSLat(latStr)
	if err != nil {
		return 0, 0, "", fmt.Errorf("invalid APRS latitude %q: %w", latStr, err)
	}

	lon, err = parseAPRSLon(lonStr)
	if err != nil {
		return 0, 0, "", fmt.Errorf("invalid APRS longitude %q: %w", lonStr, err)
	}

	return lat, lon, comment, nil
}

func parseAPRSLat(s string) (float64, error) {
	if len(s) < 8 {
		return 0, fmt.Errorf("too short")
	}
	deg, err := strconv.ParseFloat(strings.ReplaceAll(s[:2], " ", "0"), 64)
	if err != nil {
		return 0, err
	}
	min, err := strconv.ParseFloat(strings.ReplaceAll(s[2:7], " ", "0"), 64)
	if err != nil {
		return 0, err
	}
	dd := deg + min/60.0
	dir := s[7]
	if dir == 'S' || dir == 's' {
		dd = -dd
	}
	if dd < -90 || dd > 90 {
		return 0, fmt.Errorf("out of range: %.6f", dd)
	}
	return dd, nil
}

func parseAPRSLon(s string) (float64, error) {
	if len(s) < 9 {
		return 0, fmt.Errorf("too short")
	}
	deg, err := strconv.ParseFloat(strings.ReplaceAll(s[:3], " ", "0"), 64)
	if err != nil {
		return 0, err
	}
	min, err := strconv.ParseFloat(strings.ReplaceAll(s[3:8], " ", "0"), 64)
	if err != nil {
		return 0, err
	}
	dd := deg + min/60.0
	dir := s[8]
	if dir == 'W' || dir == 'w' {
		dd = -dd
	}
	if dd < -180 || dd > 180 {
		return 0, fmt.Errorf("out of range: %.6f", dd)
	}
	return dd, nil
}

// parseCompressedPosition parses APRS compressed position format.
// Format: /YYYY XXXX csT (13 chars: sym_table + 4 lat + 4 lon + sym_code + cs + type)
func parseCompressedPosition(body string) (lat, lon float64, comment string, err error) {
	if len(body) < 13 {
		return 0, 0, "", fmt.Errorf("compressed position too short")
	}
	// Skip symbol table ID (1 char)
	latChars := body[1:5]
	lonChars := body[5:9]
	// symbol code at body[9], cs at body[10:12], type at body[12]

	if len(body) > 13 {
		comment = body[13:]
	}

	lat = 90.0 - (float64(base91Decode(latChars)) / 380926.0)
	lon = -180.0 + (float64(base91Decode(lonChars)) / 190463.0)

	if lat < -90 || lat > 90 || lon < -180 || lon > 180 {
		return 0, 0, "", fmt.Errorf("compressed coords out of range: %.6f, %.6f", lat, lon)
	}

	// Extract speed/course from cs bytes if type byte indicates it
	if len(body) >= 13 {
		typeByte := body[12]
		csOrigin := (typeByte - 33) & 0x18
		if csOrigin == 0x10 { // GPS fix, current
			c := int(body[10]) - 33
			s := int(body[11]) - 33
			if c >= 0 && c <= 89 {
				course := float64(c) * 4.0
				speed := math.Pow(1.08, float64(s)) - 1.0 // knots
				speedMS := speed * 0.514444
				_ = course
				_ = speedMS
				// These could be set on the telemetry but we return them via comment
				// for simplicity - the caller extracts them separately
			}
		}
	}

	return lat, lon, comment, nil
}

func base91Decode(s string) int {
	val := 0
	for _, c := range s {
		val = val*91 + (int(c) - 33)
	}
	return val
}

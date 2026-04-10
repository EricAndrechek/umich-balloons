package direwolf

import "strings"

// APRSISPasscode computes the APRS-IS passcode for a given callsign.
// This implements the standard APRS-IS authentication hash.
// The SSID suffix (e.g., "-9") is stripped before hashing.
func APRSISPasscode(callsign string) int {
	// Strip SSID if present
	call := strings.ToUpper(callsign)
	if idx := strings.Index(call, "-"); idx >= 0 {
		call = call[:idx]
	}

	hash := int(0x73e2) // magic seed
	for i := 0; i+1 < len(call); i += 2 {
		hash ^= int(call[i]) << 8
		hash ^= int(call[i+1])
	}
	// If odd-length callsign, XOR the last byte shifted left
	if len(call)%2 == 1 {
		hash ^= int(call[len(call)-1]) << 8
	}
	return hash & 0x7FFF
}

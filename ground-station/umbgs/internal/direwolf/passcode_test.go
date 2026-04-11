package direwolf

import "testing"

func TestAPRSISPasscode(t *testing.T) {
	// Known passcodes verified against online APRS-IS passcode calculators
	tests := []struct {
		callsign string
		want     int
	}{
		{"KD8CJT", 19121},
		{"KD8CJT-9", 19121},  // SSID stripped
		{"kd8cjt", 19121},    // case insensitive
		{"kd8cjt-9", 19121},  // lowercase + SSID
		{"N0CALL", 13023},
		{"W8UPD", 13706},
		{"W8UPD-15", 13706},  // SSID stripped
	}

	for _, tt := range tests {
		got := APRSISPasscode(tt.callsign)
		if got != tt.want {
			t.Errorf("APRSISPasscode(%q) = %d, want %d", tt.callsign, got, tt.want)
		}
	}
}

func TestAPRSISPasscodeRange(t *testing.T) {
	// Passcode must always be 0-32767 (15-bit positive)
	calls := []string{"A", "AB", "ABC", "ABCDEF", "W1AW", "KD8CJT", "N0CALL"}
	for _, c := range calls {
		p := APRSISPasscode(c)
		if p < 0 || p > 32767 {
			t.Errorf("APRSISPasscode(%q) = %d, out of range 0-32767", c, p)
		}
	}
}

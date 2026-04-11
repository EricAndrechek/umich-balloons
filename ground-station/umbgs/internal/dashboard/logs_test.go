package dashboard

import (
	"testing"
	"time"
)

func TestParseJournalJSON_NonUmbgs(t *testing.T) {
	// gpsd log line — non-umbgs service, use SYSLOG_IDENTIFIER directly
	data := []byte(`{"SYSLOG_IDENTIFIER":"gpsd","MESSAGE":"listening on port 2947","PRIORITY":"6","__REALTIME_TIMESTAMP":"1712764800000000"}`)
	entry := parseJournalJSON(data)

	if entry.Service != "gpsd" {
		t.Errorf("Service = %q, want gpsd", entry.Service)
	}
	if entry.Level != "INFO" {
		t.Errorf("Level = %q, want INFO", entry.Level)
	}
	if entry.Message != "listening on port 2947" {
		t.Errorf("Message = %q, want 'listening on port 2947'", entry.Message)
	}
	// Check timestamp parsed from __REALTIME_TIMESTAMP (microseconds since epoch)
	want := time.Unix(1712764800, 0).UTC()
	if !entry.Timestamp.Equal(want) {
		t.Errorf("Timestamp = %v, want %v", entry.Timestamp, want)
	}
}

func TestParseJournalJSON_UmbgsSlog(t *testing.T) {
	// umbgs log: outer journalctl JSON wrapping inner slog JSON
	inner := `{"time":"2026-04-10T12:00:00Z","level":"WARN","msg":"connection lost","service":"aprs","error":"timeout"}`
	data := []byte(`{"SYSLOG_IDENTIFIER":"umbgs","MESSAGE":"` + escapeJSON(inner) + `","PRIORITY":"4","__REALTIME_TIMESTAMP":"1712764800000000"}`)
	entry := parseJournalJSON(data)

	if entry.Service != "aprs" {
		t.Errorf("Service = %q, want aprs", entry.Service)
	}
	if entry.Level != "WARN" {
		t.Errorf("Level = %q, want WARN", entry.Level)
	}
	if !contains(entry.Message, "connection lost") {
		t.Errorf("Message = %q, should contain 'connection lost'", entry.Message)
	}
	if !contains(entry.Message, "error=timeout") {
		t.Errorf("Message = %q, should contain 'error=timeout'", entry.Message)
	}
}

func TestParseJournalJSON_UmbgsWithSource(t *testing.T) {
	// logWriter output from direwolf subprocess — uses "source" field
	inner := `{"time":"2026-04-10T12:00:00Z","level":"INFO","msg":"Decoder started","source":"direwolf"}`
	data := []byte(`{"SYSLOG_IDENTIFIER":"umbgs","MESSAGE":"` + escapeJSON(inner) + `","PRIORITY":"6","__REALTIME_TIMESTAMP":"1712764800000000"}`)
	entry := parseJournalJSON(data)

	if entry.Service != "direwolf" {
		t.Errorf("Service = %q, want direwolf", entry.Service)
	}
}

func TestParseJournalJSON_InvalidJSON(t *testing.T) {
	data := []byte(`this is not json`)
	entry := parseJournalJSON(data)

	if entry.Message != "this is not json" {
		t.Errorf("Message = %q, want raw input", entry.Message)
	}
	if entry.Service != "system" {
		t.Errorf("Service = %q, want system (fallback)", entry.Service)
	}
}

func TestParseJournalJSON_PriorityMapping(t *testing.T) {
	tests := []struct {
		priority string
		want     string
	}{
		{"0", "ERROR"}, // emerg
		{"1", "ERROR"}, // alert
		{"2", "ERROR"}, // crit
		{"3", "ERROR"}, // err
		{"4", "WARN"},  // warning
		{"5", "INFO"},  // notice
		{"6", "INFO"},  // info
		{"7", "DEBUG"}, // debug
	}
	for _, tt := range tests {
		data := []byte(`{"SYSLOG_IDENTIFIER":"chrony","MESSAGE":"test","PRIORITY":"` + tt.priority + `"}`)
		entry := parseJournalJSON(data)
		if entry.Level != tt.want {
			t.Errorf("priority %s → Level = %q, want %q", tt.priority, entry.Level, tt.want)
		}
	}
}

func TestCleanMessage(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"hello world", "hello world"},
		{"hello\\nworld", "hello world"},
		{"hello\nworld", "hello world"},
		{"too   many    spaces", "too many spaces"},
		{"  leading and trailing  ", "leading and trailing"},
		{"tabs\\there", "tabs here"},
		{"carriage\\rreturn", "carriagereturn"},
	}
	for _, tt := range tests {
		got := cleanMessage(tt.input)
		if got != tt.want {
			t.Errorf("cleanMessage(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestParseInt64(t *testing.T) {
	tests := []struct {
		input string
		want  int64
	}{
		{"1712764800000000", 1712764800000000},
		{"0", 0},
		{"123", 123},
	}
	for _, tt := range tests {
		got := parseInt64(tt.input)
		if got != tt.want {
			t.Errorf("parseInt64(%q) = %d, want %d", tt.input, got, tt.want)
		}
	}
}

func TestFormatFloat(t *testing.T) {
	tests := []struct {
		input float64
		want  string
	}{
		{42.0, "42"},
		{3.14, "3.14"},
		{0.0, "0"},
		{100.5, "100.5"},
	}
	for _, tt := range tests {
		got := formatFloat(tt.input)
		if got != tt.want {
			t.Errorf("formatFloat(%f) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

// escapeJSON escapes a string for embedding in a JSON string value.
func escapeJSON(s string) string {
	out := ""
	for _, c := range s {
		switch c {
		case '"':
			out += `\"`
		case '\\':
			out += `\\`
		default:
			out += string(c)
		}
	}
	return out
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && searchString(s, substr)
}

func searchString(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

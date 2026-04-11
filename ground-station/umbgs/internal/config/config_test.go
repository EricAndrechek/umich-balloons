package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaults(t *testing.T) {
	cfg := Defaults()
	if err := cfg.Validate(); err != nil {
		t.Fatalf("Defaults() should be valid: %v", err)
	}
	if cfg.SSID != 9 {
		t.Errorf("default SSID = %d, want 9", cfg.SSID)
	}
	if cfg.APRS.Frequency != 144.390 {
		t.Errorf("default APRS frequency = %f, want 144.390", cfg.APRS.Frequency)
	}
	if cfg.Dashboard.Port != 8080 {
		t.Errorf("default dashboard port = %d, want 8080", cfg.Dashboard.Port)
	}
}

func TestValidate(t *testing.T) {
	tests := []struct {
		name    string
		modify  func(*Config)
		wantErr bool
	}{
		{
			name:    "defaults valid",
			modify:  func(c *Config) {},
			wantErr: false,
		},
		{
			name:    "SSID too high",
			modify:  func(c *Config) { c.SSID = 16 },
			wantErr: true,
		},
		{
			name:    "SSID negative",
			modify:  func(c *Config) { c.SSID = -1 },
			wantErr: true,
		},
		{
			name:    "empty API URL",
			modify:  func(c *Config) { c.APIUrl = "" },
			wantErr: true,
		},
		{
			name:    "APRS bad port",
			modify:  func(c *Config) { c.APRS.KISSPort = 0 },
			wantErr: true,
		},
		{
			name:    "APRS port too high",
			modify:  func(c *Config) { c.APRS.KISSPort = 70000 },
			wantErr: true,
		},
		{
			name:    "APRS empty host",
			modify:  func(c *Config) { c.APRS.KISSHost = "" },
			wantErr: true,
		},
		{
			name:    "APRS disabled skips validation",
			modify:  func(c *Config) { c.APRS.Enabled = false; c.APRS.KISSHost = "" },
			wantErr: false,
		},
		{
			name:    "LoRa bad baud",
			modify:  func(c *Config) { c.LoRa.Baud = 0 },
			wantErr: true,
		},
		{
			name:    "LoRa disabled skips validation",
			modify:  func(c *Config) { c.LoRa.Enabled = false; c.LoRa.Baud = 0 },
			wantErr: false,
		},
		{
			name:    "GPS bad interval",
			modify:  func(c *Config) { c.GPS.ReportInterval = -1 },
			wantErr: true,
		},
		{
			name:    "dashboard bad port",
			modify:  func(c *Config) { c.Dashboard.Port = 99999 },
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := Defaults()
			tt.modify(&cfg)
			err := cfg.Validate()
			if (err != nil) != tt.wantErr {
				t.Errorf("Validate() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestUploaderCallsign(t *testing.T) {
	tests := []struct {
		callsign string
		ssid     int
		want     string
	}{
		{"KD8CJT", 9, "KD8CJT-9"},
		{"kd8cjt", 9, "KD8CJT-9"},
		{"KD8CJT", 0, "KD8CJT"},
		{"W8UPD", 15, "W8UPD-15"},
	}
	for _, tt := range tests {
		cfg := Config{Callsign: tt.callsign, SSID: tt.ssid}
		got := cfg.UploaderCallsign()
		if got != tt.want {
			t.Errorf("UploaderCallsign(%q, %d) = %q, want %q", tt.callsign, tt.ssid, got, tt.want)
		}
	}
}

func TestConfigured(t *testing.T) {
	tests := []struct {
		callsign string
		want     bool
	}{
		{"", false},
		{"CHANGE_ME", false},
		{"KD8CJT", true},
	}
	for _, tt := range tests {
		cfg := Config{Callsign: tt.callsign}
		if got := cfg.Configured(); got != tt.want {
			t.Errorf("Configured(%q) = %v, want %v", tt.callsign, got, tt.want)
		}
	}
}

func TestSanitized(t *testing.T) {
	cfg := Defaults()
	cfg.WiFi.Networks = []WiFiNetwork{
		{SSID: "MyNetwork", PSK: "secret123"},
		{SSID: "Other", PSK: "hunter2"},
	}
	san := cfg.Sanitized()

	for i, n := range san.WiFi.Networks {
		if n.PSK != "********" {
			t.Errorf("Sanitized network %d PSK = %q, want ********", i, n.PSK)
		}
		if n.SSID != cfg.WiFi.Networks[i].SSID {
			t.Errorf("Sanitized network %d SSID = %q, want %q", i, n.SSID, cfg.WiFi.Networks[i].SSID)
		}
	}

	// Original should be unchanged
	if cfg.WiFi.Networks[0].PSK != "secret123" {
		t.Error("Sanitized mutated original config")
	}
}

func TestLoadFromYAML(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")

	yaml := `callsign: W8UPD
ssid: 5
api_url: https://example.com
aprs:
  enabled: false
lora:
  enabled: false
gps:
  enabled: false
dashboard:
  enabled: false
`
	if err := os.WriteFile(path, []byte(yaml), 0644); err != nil {
		t.Fatal(err)
	}

	cfg, err := loadFromFile(path)
	if err != nil {
		t.Fatalf("loadFromFile: %v", err)
	}
	if cfg.Callsign != "W8UPD" {
		t.Errorf("Callsign = %q, want W8UPD", cfg.Callsign)
	}
	if cfg.SSID != 5 {
		t.Errorf("SSID = %d, want 5", cfg.SSID)
	}
	// Unset fields should get defaults
	if cfg.LogLevel != "info" {
		t.Errorf("LogLevel = %q, want info (default)", cfg.LogLevel)
	}
}

func TestLoadFromYAMLInvalid(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")

	if err := os.WriteFile(path, []byte("not: valid: yaml: ["), 0644); err != nil {
		t.Fatal(err)
	}

	_, err := loadFromFile(path)
	if err == nil {
		t.Error("loadFromFile should fail on invalid YAML")
	}
}

func TestManagerVersioning(t *testing.T) {
	cfg := Defaults()
	m := NewManager(&cfg, "")

	if v := m.Version(); v != 1 {
		t.Errorf("initial version = %d, want 1", v)
	}

	got := m.Get()
	if got.SSID != 9 {
		t.Errorf("Get().SSID = %d, want 9", got.SSID)
	}
}

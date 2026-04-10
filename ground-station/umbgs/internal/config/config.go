// Package config handles loading and validating ground station configuration.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"gopkg.in/yaml.v3"
)

// Config is the top-level ground station configuration.
type Config struct {
	Callsign string `yaml:"callsign" json:"callsign"`
	SSID     int    `yaml:"ssid" json:"ssid"`
	APIUrl   string `yaml:"api_url" json:"api_url"`

	WiFi      WiFiConfig      `yaml:"wifi" json:"wifi"`
	APRS      APRSConfig      `yaml:"aprs" json:"aprs"`
	LoRa      LoRaConfig      `yaml:"lora" json:"lora"`
	GPS       GPSConfig       `yaml:"gps" json:"gps"`
	Dashboard DashboardConfig `yaml:"dashboard" json:"dashboard"`
	Display   DisplayConfig   `yaml:"display" json:"display"`
	Update    UpdateConfig    `yaml:"update" json:"update"`
	LogLevel  string          `yaml:"log_level" json:"log_level"`
}

type WiFiConfig struct {
	Networks []WiFiNetwork `yaml:"networks" json:"networks"`
}

type WiFiNetwork struct {
	SSID string `yaml:"ssid" json:"ssid"`
	PSK  string `yaml:"psk" json:"psk"`
}

type APRSConfig struct {
	Enabled   bool    `yaml:"enabled" json:"enabled"`
	KISSHost  string  `yaml:"kiss_host" json:"kiss_host"`
	KISSPort  int     `yaml:"kiss_port" json:"kiss_port"`
	Frequency float64 `yaml:"frequency" json:"frequency"`
	Gain      int     `yaml:"gain" json:"gain"`
	IGServer  string  `yaml:"igserver" json:"igserver"`
	// Beacon settings for iGate position reporting
	BeaconComment string  `yaml:"beacon_comment" json:"beacon_comment"`
	BeaconLat     float64 `yaml:"beacon_lat" json:"beacon_lat"`
	BeaconLon     float64 `yaml:"beacon_lon" json:"beacon_lon"`
	BeaconAlt     int     `yaml:"beacon_alt" json:"beacon_alt"`
}

type LoRaConfig struct {
	Enabled bool   `yaml:"enabled" json:"enabled"`
	Baud    int    `yaml:"baud" json:"baud"`
	Device  string `yaml:"device" json:"device"`
}

type GPSConfig struct {
	Enabled        bool `yaml:"enabled" json:"enabled"`
	ReportInterval int  `yaml:"report_interval" json:"report_interval"`
}

type DashboardConfig struct {
	Enabled bool `yaml:"enabled" json:"enabled"`
	Port    int  `yaml:"port" json:"port"`
}

type DisplayConfig struct {
	Enabled bool   `yaml:"enabled" json:"enabled"`
	URL     string `yaml:"url" json:"url"`
}

type UpdateConfig struct {
	Enabled bool   `yaml:"enabled" json:"enabled"`
	Channel string `yaml:"channel" json:"channel"`
}

// Defaults returns a Config with sensible defaults.
func Defaults() Config {
	return Config{
		Callsign: "",
		SSID:     9,
		APIUrl:   "https://api.umich-balloons.com",
		APRS: APRSConfig{
			Enabled:       true,
			KISSHost:      "127.0.0.1",
			KISSPort:      8001,
			Frequency:     144.390,
			Gain:          0, // 0 = auto gain
			IGServer:      "noam.aprs2.net",
			BeaconComment: "UMich Balloons Ground Station",
			BeaconLat:     42.2943757,
			BeaconLon:     -83.7110013,
			BeaconAlt:     271,
		},
		LoRa: LoRaConfig{
			Enabled: true,
			Baud:    9600,
		},
		GPS: GPSConfig{
			Enabled:        true,
			ReportInterval: 60,
		},
		Dashboard: DashboardConfig{
			Enabled: true,
			Port:    8080,
		},
		Display: DisplayConfig{
			Enabled: false,
			URL:     "http://localhost:8080",
		},
		Update: UpdateConfig{
			Enabled: true,
			Channel: "stable",
		},
		LogLevel: "info",
	}
}

// UploaderCallsign returns the combined callsign-SSID string (e.g., "KD8CJT-9").
func (c *Config) UploaderCallsign() string {
	if c.SSID > 0 {
		return fmt.Sprintf("%s-%d", strings.ToUpper(c.Callsign), c.SSID)
	}
	return strings.ToUpper(c.Callsign)
}

// Configured returns true if the callsign has been set to a real value.
func (c *Config) Configured() bool {
	return c.Callsign != "" && c.Callsign != "CHANGE_ME"
}

// Validate checks the config for errors and returns a descriptive error if invalid.
// An unconfigured callsign is allowed — the binary will start in setup mode.
func (c *Config) Validate() error {
	if c.SSID < 0 || c.SSID > 15 {
		return fmt.Errorf("ssid must be 0-15, got %d", c.SSID)
	}
	if c.APIUrl == "" {
		return fmt.Errorf("api_url must not be empty")
	}
	if c.APRS.Enabled {
		if c.APRS.KISSHost == "" {
			return fmt.Errorf("aprs.kiss_host must not be empty when APRS is enabled")
		}
		if c.APRS.KISSPort <= 0 || c.APRS.KISSPort > 65535 {
			return fmt.Errorf("aprs.kiss_port must be 1-65535, got %d", c.APRS.KISSPort)
		}
	}
	if c.LoRa.Enabled {
		if c.LoRa.Baud <= 0 {
			return fmt.Errorf("lora.baud must be positive, got %d", c.LoRa.Baud)
		}
	}
	if c.GPS.Enabled {
		if c.GPS.ReportInterval <= 0 {
			return fmt.Errorf("gps.report_interval must be positive, got %d", c.GPS.ReportInterval)
		}
	}
	if c.Dashboard.Enabled {
		if c.Dashboard.Port <= 0 || c.Dashboard.Port > 65535 {
			return fmt.Errorf("dashboard.port must be 1-65535, got %d", c.Dashboard.Port)
		}
	}
	return nil
}

// Sanitized returns a copy with sensitive fields masked (WiFi PSKs).
func (c *Config) Sanitized() Config {
	out := *c
	out.WiFi.Networks = make([]WiFiNetwork, len(c.WiFi.Networks))
	for i, n := range c.WiFi.Networks {
		out.WiFi.Networks[i] = WiFiNetwork{
			SSID: n.SSID,
			PSK:  "********",
		}
	}
	return out
}

const (
	// BootConfigPath is the user-editable config on the FAT32 boot partition.
	BootConfigPath = "/boot/firmware/ground-station.yaml"
	// DataConfigPath is the runtime config on the writable data partition.
	DataConfigPath = "/data/config.yaml"
)

// Load reads config from disk following the precedence rules:
// 1. /data/config.yaml (runtime copy, includes web UI changes)
// 2. /boot/firmware/ground-station.yaml (first boot seed)
// 3. Compiled-in defaults
func Load() (*Config, string, error) {
	// Try runtime config first
	if cfg, err := loadFromFile(DataConfigPath); err == nil {
		if err := cfg.Validate(); err != nil {
			return nil, DataConfigPath, fmt.Errorf("config validation error in %s: %w", DataConfigPath, err)
		}
		return cfg, DataConfigPath, nil
	}

	// Try boot partition (first boot) — copy to /data/
	if cfg, err := loadFromFile(BootConfigPath); err == nil {
		// Ensure /data/ directory exists
		if err := os.MkdirAll(filepath.Dir(DataConfigPath), 0755); err != nil {
			return nil, BootConfigPath, fmt.Errorf("failed to create data dir: %w", err)
		}
		// Copy to runtime location
		data, _ := os.ReadFile(BootConfigPath)
		if err := os.WriteFile(DataConfigPath, data, 0644); err != nil {
			return nil, BootConfigPath, fmt.Errorf("failed to copy config to %s: %w", DataConfigPath, err)
		}
		if err := cfg.Validate(); err != nil {
			return nil, DataConfigPath, fmt.Errorf("config validation error: %w", err)
		}
		return cfg, DataConfigPath, nil
	}

	// Use defaults
	cfg := Defaults()
	return &cfg, "", nil
}

func loadFromFile(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	cfg := Defaults() // Start from defaults so unset fields get default values
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("failed to parse %s: %w", path, err)
	}
	return &cfg, nil
}

// Save writes the config to the runtime path (/data/config.yaml).
func Save(cfg *Config) error {
	data, err := yaml.Marshal(cfg)
	if err != nil {
		return fmt.Errorf("failed to marshal config: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(DataConfigPath), 0755); err != nil {
		return fmt.Errorf("failed to create data dir: %w", err)
	}
	return os.WriteFile(DataConfigPath, data, 0644)
}

// Manager provides thread-safe access to the current config and hot-reload capability.
type Manager struct {
	mu   sync.RWMutex
	cfg  *Config
	path string
}

// NewManager creates a config manager with the given initial config.
func NewManager(cfg *Config, path string) *Manager {
	return &Manager{cfg: cfg, path: path}
}

// Get returns a copy of the current config.
func (m *Manager) Get() Config {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return *m.cfg
}

// Update replaces the config, saves to disk, and returns fields that require a restart.
func (m *Manager) Update(newCfg *Config) (restartNeeded []string, err error) {
	if err := newCfg.Validate(); err != nil {
		return nil, err
	}

	m.mu.Lock()
	old := m.cfg
	m.cfg = newCfg
	m.mu.Unlock()

	if err := Save(newCfg); err != nil {
		return nil, err
	}

	// Determine what changed and needs a restart
	if old.APRS != newCfg.APRS {
		restartNeeded = append(restartNeeded, "aprs")
	}
	if old.LoRa != newCfg.LoRa {
		restartNeeded = append(restartNeeded, "lora")
	}
	if old.Callsign != newCfg.Callsign || old.SSID != newCfg.SSID {
		restartNeeded = append(restartNeeded, "all")
	}
	if old.Dashboard.Port != newCfg.Dashboard.Port {
		restartNeeded = append(restartNeeded, "dashboard")
	}

	return restartNeeded, nil
}

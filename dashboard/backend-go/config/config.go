package config

import (
	"bufio"
	"os"
	"strconv"
	"strings"
)

func init() {
	// Load .env file if present (does NOT override existing env vars)
	loadDotEnv(".env")
}

func loadDotEnv(path string) {
	f, err := os.Open(path)
	if err != nil {
		return // no .env file, that's fine
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		// Don't override existing env vars
		if os.Getenv(key) == "" {
			os.Setenv(key, val)
		}
	}
}

// Config holds all configuration for the application.
type Config struct {
	ListenAddr                string
	SondehubAPIURL            string
	SoftwareName              string
	SoftwareVersion           string
	DevMode                   bool
	UploadInterval            int
	GroundControlPublicKeyPEM string
}

// Load reads configuration from environment variables and optional files.
func Load() *Config {
	cfg := &Config{
		ListenAddr:                envOrDefault("LISTEN_ADDR", ":8080"),
		SondehubAPIURL:            envOrDefault("SONDEHUB_API_URL", "https://api.v2.sondehub.org"),
		SoftwareName:              envOrDefault("SOFTWARE_NAME", "umich-balloons"),
		SoftwareVersion:           envOrDefault("SOFTWARE_VERSION", "2.0.0"),
		DevMode:                   envOrDefault("DEV_MODE", "false") == "true",
		UploadInterval:            envIntOrDefault("UPLOAD_INTERVAL", 2),
		GroundControlPublicKeyPEM: envOrDefault("GROUND_CONTROL_PUBLIC_KEY", defaultPublicKey),
	}

	return cfg
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envIntOrDefault(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

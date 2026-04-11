// Package lora reads LoRa telemetry from a USB serial device.
package lora

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"go.bug.st/serial"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// Reader reads LoRa telemetry lines from a USB serial device.
type Reader struct {
	cfg    *config.Manager
	out    chan<- types.Packet
	logger *slog.Logger
}

// NewReader creates a new LoRa serial reader.
func NewReader(cfg *config.Manager, out chan<- types.Packet, logger *slog.Logger) *Reader {
	return &Reader{cfg: cfg, out: out, logger: logger.With("service", "lora")}
}

// Run reads serial data until ctx is cancelled. Reconnects automatically.
func (r *Reader) Run(ctx context.Context) error {
	for {
		c := r.cfg.Get()
		if !c.LoRa.Enabled {
			r.logger.Info("LoRa disabled, waiting for config change")
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(30 * time.Second):
				continue
			}
		}

		err := r.readLoop(ctx, c)
		if ctx.Err() != nil {
			return ctx.Err()
		}

		// Detect missing hardware vs busy device for appropriate logging/backoff
		backoff := 10 * time.Second
		errMsg := strings.ToLower(err.Error())
		if strings.Contains(errMsg, "no known usb serial device") ||
			strings.Contains(errMsg, "cannot list") ||
			strings.Contains(errMsg, "no such file") {
			r.logger.Debug("LoRa hardware not found, retrying in 60s", "error", err)
			backoff = 60 * time.Second
		} else if strings.Contains(errMsg, "busy") {
			r.logger.Warn("LoRa serial device busy, retrying in 15s", "error", err)
			backoff = 15 * time.Second
		} else {
			r.logger.Warn("serial connection lost, reconnecting", "error", err, "backoff", backoff)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
	}
}

func (r *Reader) readLoop(ctx context.Context, c config.Config) error {
	device := c.LoRa.Device
	if device == "" {
		var err error
		device, err = detectDevice()
		if err != nil {
			return err
		}
	}

	r.logger.Info("opening serial device", "device", device, "baud", c.LoRa.Baud)
	port, err := serial.Open(device, &serial.Mode{BaudRate: c.LoRa.Baud})
	if err != nil {
		return fmt.Errorf("open %s: %w", device, err)
	}
	defer port.Close()

	// Close the port when context is cancelled so blocking reads unblock.
	closeDone := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			port.Close()
		case <-closeDone:
		}
	}()
	defer close(closeDone)

	// Allow Arduino to reset after connection
	time.Sleep(2 * time.Second)
	r.logger.Info("serial connected")

	scanner := bufio.NewScanner(port)
	callsign := c.UploaderCallsign()

	for scanner.Scan() {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		if strings.HasPrefix(line, "[DEBUG]") {
			r.logger.Debug("skipping debug line", "line", line)
			continue
		}

		r.logger.Info("LoRa data received", "raw", line)

		// Try to parse as JSON to get structured fields
		var parsed map[string]interface{}
		if err := json.Unmarshal([]byte(line), &parsed); err != nil {
			r.logger.Debug("line is not JSON, sending as raw", "error", err)
		}

		// Drop packets the balloon sent before it had a GPS fix — the API
		// requires a usable position and would reject these with HTTP 400.
		// Sending them anyway wastes bandwidth and clutters the failed-packet
		// table. Malformed packets (non-JSON, bad fields, etc.) still fall
		// through to the uploader, which will record any server-side 4xx in
		// the failed table.
		if parsed != nil && !hasUsablePosition(parsed) {
			r.logger.Info("dropping LoRa packet with no GPS fix", "raw", line)
			continue
		}

		pkt := types.Packet{
			Source:   "lora",
			RawData:  line,
			Parsed:   parsed,
			Endpoint: "/lora",
			Sender:   callsign,
			Time:     time.Now().UTC(),
		}

		select {
		case r.out <- pkt:
		case <-ctx.Done():
			return ctx.Err()
		}
	}

	if err := scanner.Err(); err != nil {
		return fmt.Errorf("scanner: %w", err)
	}
	return fmt.Errorf("serial device closed")
}

// latAliases / lonAliases mirror the field aliases accepted by the API's
// parseLoRaJSON (see api/src/normalize.ts). Keeping these in sync with the
// API's validation rules is what makes the source-side pre-fix filter safe.
var latAliases = []string{"latitude", "lat", "latitude_deg", "lat_deg", "lat_dd"}
var lonAliases = []string{"longitude", "lon", "longitude_deg", "lon_deg", "lon_dd"}

// hasUsablePosition returns true if the parsed LoRa JSON contains a latitude
// and longitude that the API will accept. The API rejects exact 0,0 and
// missing fields; it tolerates any other values including the compact
// integer-scaled format (e.g. 422949 meaning 42.2949°N).
//
// When in doubt, return true — the uploader will record any server-side
// rejection in the failed table, so the only cost of a false positive is
// one wasted API call.
func hasUsablePosition(parsed map[string]interface{}) bool {
	lat, latOK := lookupNumber(parsed, latAliases)
	lon, lonOK := lookupNumber(parsed, lonAliases)
	if !latOK || !lonOK {
		return false
	}
	// Exact 0,0 is the "no fix" marker the API rejects. A tiny epsilon
	// avoids the edge case of a firmware that emits e.g. 0.0000001.
	if lat == 0 && lon == 0 {
		return false
	}
	return true
}

// lookupNumber searches parsed for any of aliases and returns the value as
// a float64 if it can be converted. Returns (0, false) if no alias resolved
// to a numeric value.
func lookupNumber(parsed map[string]interface{}, aliases []string) (float64, bool) {
	for _, key := range aliases {
		v, ok := parsed[key]
		if !ok {
			// JSON is case-sensitive, but some firmwares use different
			// casing — check lowercase too.
			v, ok = parsed[strings.ToLower(key)]
			if !ok {
				continue
			}
		}
		switch n := v.(type) {
		case float64:
			return n, true
		case int:
			return float64(n), true
		case int64:
			return float64(n), true
		case string:
			// Some firmwares emit numbers as strings. Try to parse.
			var f float64
			if _, err := fmt.Sscanf(n, "%f", &f); err == nil {
				return f, true
			}
		}
	}
	return 0, false
}

// knownSerialIDs contains substrings to match against /dev/serial/by-id/ entries.
// Matches vendor IDs, chip names, and common USB-serial adapter identifiers.
var knownSerialIDs = []string{
	"arduino",
	"ch340", "ch341",
	"1a86",  // QinHeng CH340/CH341 vendor ID
	"ftdi",
	"cp210",
	"serial", // catches "USB2.0-Serial" and similar generic names
}

// excludeSerialIDs filters out devices that look like GPS receivers, not LoRa.
var excludeSerialIDs = []string{
	"garmin",
	"091e", // Garmin vendor ID
	"u-blox",
	"gps",
}

// detectDevice scans /dev/serial/by-id/ for known USB serial adapters,
// excluding GPS receivers and other non-LoRa devices.
func detectDevice() (string, error) {
	byID := "/dev/serial/by-id"
	entries, err := os.ReadDir(byID)
	if err != nil {
		return "", fmt.Errorf("cannot list %s: %w", byID, err)
	}

	for _, e := range entries {
		name := strings.ToLower(e.Name())

		// Skip known non-LoRa devices (GPS, etc.)
		excluded := false
		for _, ex := range excludeSerialIDs {
			if strings.Contains(name, ex) {
				excluded = true
				break
			}
		}
		if excluded {
			continue
		}

		// Check if it matches a known serial adapter
		for _, id := range knownSerialIDs {
			if strings.Contains(name, id) {
				path := filepath.Join(byID, e.Name())
				resolved, err := filepath.EvalSymlinks(path)
				if err != nil {
					return path, nil
				}
				return resolved, nil
			}
		}
	}

	return "", fmt.Errorf("no known USB serial device found in %s", byID)
}

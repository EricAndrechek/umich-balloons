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
		r.logger.Warn("serial connection lost, reconnecting", "error", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(10 * time.Second):
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

// detectDevice scans /dev/serial/by-id/ for known USB serial adapters.
func detectDevice() (string, error) {
	byID := "/dev/serial/by-id"
	entries, err := os.ReadDir(byID)
	if err != nil {
		return "", fmt.Errorf("cannot list %s: %w", byID, err)
	}

	knownChips := []string{"Arduino", "CH340", "FTDI", "USB-Serial", "CP210"}
	for _, e := range entries {
		name := e.Name()
		for _, chip := range knownChips {
			if strings.Contains(strings.ToLower(name), strings.ToLower(chip)) {
				path := filepath.Join(byID, name)
				// Resolve symlink to actual device
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

// Package gps connects to gpsd and reports station position to the API.
package gps

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/vmihailenco/msgpack/v5"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// tpvReport is the gpsd TPV (Time-Position-Velocity) JSON message.
type tpvReport struct {
	Class  string  `json:"class"`
	Lat    float64 `json:"lat"`
	Lon    float64 `json:"lon"`
	AltHAE float64 `json:"altHAE"`
	Alt    float64 `json:"alt"`
	Mode   int     `json:"mode"` // 0=unknown, 1=nofix, 2=2D, 3=3D
}

// Reporter connects to gpsd and periodically reports station position.
type Reporter struct {
	cfg    *config.Manager
	logger *slog.Logger

	mu  sync.RWMutex
	pos *types.Position
}

// NewReporter creates a GPS reporter.
func NewReporter(cfg *config.Manager, logger *slog.Logger) *Reporter {
	return &Reporter{cfg: cfg, logger: logger.With("service", "gps")}
}

// Position returns the latest GPS fix, or nil if no fix.
func (r *Reporter) Position() *types.Position {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.pos == nil {
		return nil
	}
	p := *r.pos
	return &p
}

// Run connects to gpsd and updates the position continuously.
// It also starts a goroutine to periodically upload position to the API.
func (r *Reporter) Run(ctx context.Context) error {
	// Start position upload goroutine
	go r.uploadLoop(ctx)

	for {
		c := r.cfg.Get()
		if !c.GPS.Enabled {
			r.logger.Info("GPS disabled, waiting for config change")
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(30 * time.Second):
				continue
			}
		}

		err := r.readLoop(ctx)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		r.logger.Warn("gpsd connection lost, reconnecting", "error", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(10 * time.Second):
		}
	}
}

func (r *Reporter) readLoop(ctx context.Context) error {
	r.logger.Info("connecting to gpsd", "addr", "localhost:2947")

	var d net.Dialer
	d.Timeout = 10 * time.Second
	conn, err := d.DialContext(ctx, "tcp", "localhost:2947")
	if err != nil {
		return fmt.Errorf("dial gpsd: %w", err)
	}
	defer conn.Close()

	// Enable watch mode
	_, err = conn.Write([]byte(`?WATCH={"enable":true,"json":true}` + "\n"))
	if err != nil {
		return fmt.Errorf("send WATCH: %w", err)
	}
	r.logger.Info("connected to gpsd, WATCH enabled")

	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 0, 8192), 8192)

	for scanner.Scan() {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		line := scanner.Bytes()
		var msg struct {
			Class string `json:"class"`
		}
		if err := json.Unmarshal(line, &msg); err != nil {
			continue
		}

		if msg.Class != "TPV" {
			continue
		}

		var tpv tpvReport
		if err := json.Unmarshal(line, &tpv); err != nil {
			r.logger.Warn("failed to parse TPV", "error", err)
			continue
		}

		// Need at least a 2D fix
		if tpv.Mode < 2 {
			continue
		}

		alt := tpv.AltHAE
		if alt == 0 {
			alt = tpv.Alt
		}

		pos := types.Position{
			Lat:  tpv.Lat,
			Lon:  tpv.Lon,
			Alt:  alt,
			Time: time.Now().UTC(),
		}

		r.mu.Lock()
		r.pos = &pos
		r.mu.Unlock()

		r.logger.Debug("GPS fix updated", "lat", pos.Lat, "lon", pos.Lon, "alt", pos.Alt)
	}

	if err := scanner.Err(); err != nil {
		return fmt.Errorf("gpsd scanner: %w", err)
	}
	return fmt.Errorf("gpsd closed connection")
}

// uploadLoop periodically uploads the station position to the API.
func (r *Reporter) uploadLoop(ctx context.Context) {
	c := r.cfg.Get()
	interval := time.Duration(c.GPS.ReportInterval) * time.Second
	if interval <= 0 {
		interval = 60 * time.Second
	}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	client := &http.Client{Timeout: 15 * time.Second}

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			pos := r.Position()
			if pos == nil {
				continue
			}
			if err := r.uploadPosition(ctx, client, pos); err != nil {
				r.logger.Warn("failed to upload station position", "error", err)
			} else {
				r.logger.Debug("station position uploaded", "lat", pos.Lat, "lon", pos.Lon)
			}
		}
	}
}

func (r *Reporter) uploadPosition(ctx context.Context, client *http.Client, pos *types.Position) error {
	c := r.cfg.Get()
	url := c.APIUrl + "/station"

	payload := map[string]interface{}{
		"callsign":  c.UploaderCallsign(),
		"lat":       pos.Lat,
		"lon":       pos.Lon,
		"alt":       pos.Alt,
		"timestamp": pos.Time.UTC().Format(time.RFC3339Nano),
	}

	body, err := encodeMsgpackGzip(payload)
	if err != nil {
		return fmt.Errorf("encode: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/msgpack")
	req.Header.Set("Content-Encoding", "gzip")

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("request: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	return fmt.Errorf("HTTP %d", resp.StatusCode)
}

func encodeMsgpackGzip(v interface{}) ([]byte, error) {
	msgpackData, err := msgpack.Marshal(v)
	if err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if _, err := gz.Write(msgpackData); err != nil {
		return nil, err
	}
	if err := gz.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

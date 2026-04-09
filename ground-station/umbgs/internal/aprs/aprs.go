// Package aprs connects to Direwolf's KISS TCP port and emits raw APRS packets.
package aprs

import (
	"bufio"
	"context"
	"fmt"
	"log/slog"
	"net"
	"strings"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// Common Direwolf status prefixes to ignore.
var ignorePrefixes = []string{
	"[", "Audio input level", "***", "Digipeater", "Dire Wolf",
	"Copyright", "Ready", "Channel", "Current TNC", "Sending",
	"Valid", "Fixed", "Unknown", "WARNING", "Too", "Position",
	"Rate", "Received", "Loading", "Set up", "Note:", "AGWPE",
	"KISS",
}

// Listener reads APRS data from a Direwolf KISS TCP socket.
type Listener struct {
	cfg    *config.Manager
	out    chan<- types.Packet
	logger *slog.Logger
}

// NewListener creates a new APRS listener that sends packets to the given channel.
func NewListener(cfg *config.Manager, out chan<- types.Packet, logger *slog.Logger) *Listener {
	return &Listener{cfg: cfg, out: out, logger: logger.With("service", "aprs")}
}

// Run connects to Direwolf and reads packets until ctx is cancelled.
// It reconnects automatically on disconnect with exponential backoff.
func (l *Listener) Run(ctx context.Context) error {
	for {
		c := l.cfg.Get()
		if !c.APRS.Enabled {
			l.logger.Info("APRS disabled, waiting for config change")
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(30 * time.Second):
				continue
			}
		}

		addr := fmt.Sprintf("%s:%d", c.APRS.KISSHost, c.APRS.KISSPort)
		err := l.readLoop(ctx, addr, c.UploaderCallsign())
		if ctx.Err() != nil {
			return ctx.Err()
		}
		l.logger.Warn("Direwolf connection lost, reconnecting", "error", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(15 * time.Second):
		}
	}
}

func (l *Listener) readLoop(ctx context.Context, addr, callsign string) error {
	l.logger.Info("connecting to Direwolf", "addr", addr)

	var d net.Dialer
	d.Timeout = 10 * time.Second
	conn, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return fmt.Errorf("dial %s: %w", addr, err)
	}
	defer conn.Close()
	l.logger.Info("connected to Direwolf")

	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 0, 4096), 4096)

	for scanner.Scan() {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		if isNoise(line) {
			l.logger.Debug("ignoring noise", "line", line)
			continue
		}

		if !strings.Contains(line, ">") {
			l.logger.Debug("no '>' in line, skipping", "line", line)
			continue
		}

		l.logger.Info("APRS packet received", "raw", line)
		pkt := types.Packet{
			Source:   "aprs",
			RawData:  line,
			Endpoint: "/aprs",
			Sender:   callsign,
			Time:     time.Now().UTC(),
		}

		select {
		case l.out <- pkt:
		case <-ctx.Done():
			return ctx.Err()
		}
	}

	if err := scanner.Err(); err != nil {
		return fmt.Errorf("scanner: %w", err)
	}
	return fmt.Errorf("Direwolf closed connection")
}

func isNoise(line string) bool {
	for _, p := range ignorePrefixes {
		if strings.HasPrefix(line, p) {
			return true
		}
	}
	return false
}

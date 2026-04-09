package dashboard

import (
	"bufio"
	"context"
	"log/slog"
	"os/exec"
	"strings"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// LogAggregator streams logs from journalctl and feeds them to the hub.
type LogAggregator struct {
	hub    *Hub
	logger *slog.Logger
}

// NewLogAggregator creates a log aggregator.
func NewLogAggregator(hub *Hub, logger *slog.Logger) *LogAggregator {
	return &LogAggregator{hub: hub, logger: logger.With("service", "logs")}
}

// Run starts streaming journal logs until ctx is cancelled.
func (la *LogAggregator) Run(ctx context.Context) error {
	units := []string{"umbgs", "direwolf", "gpsd", "chrony"}

	for {
		err := la.stream(ctx, units)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		la.logger.Warn("journalctl exited, restarting", "error", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(5 * time.Second):
		}
	}
}

func (la *LogAggregator) stream(ctx context.Context, units []string) error {
	args := []string{"--follow", "--no-pager", "--output=short-iso", "--lines=0"}
	for _, u := range units {
		args = append(args, "-u", u)
	}

	cmd := exec.CommandContext(ctx, "journalctl", args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}

	if err := cmd.Start(); err != nil {
		return err
	}

	scanner := bufio.NewScanner(stdout)
	for scanner.Scan() {
		line := scanner.Text()
		entry := parseLine(line)
		la.hub.BroadcastLog(entry)
	}

	return cmd.Wait()
}

func parseLine(line string) types.LogEntry {
	entry := types.LogEntry{
		Timestamp: time.Now().UTC(),
		Service:   "system",
		Level:     "INFO",
		Message:   line,
	}

	// Try to extract service from systemd format: "date host service[pid]: message"
	parts := strings.SplitN(line, ": ", 2)
	if len(parts) == 2 {
		entry.Message = parts[1]
		header := parts[0]

		// Extract service name from "date host service[pid]"
		fields := strings.Fields(header)
		if len(fields) >= 3 {
			svc := fields[len(fields)-1]
			if idx := strings.Index(svc, "["); idx > 0 {
				svc = svc[:idx]
			}
			entry.Service = svc
		}
	}

	// Detect log level from message content
	upper := strings.ToUpper(entry.Message)
	switch {
	case strings.Contains(upper, "ERROR") || strings.Contains(upper, "ERR"):
		entry.Level = "ERROR"
	case strings.Contains(upper, "WARN"):
		entry.Level = "WARN"
	case strings.Contains(upper, "DEBUG"):
		entry.Level = "DEBUG"
	}

	return entry
}

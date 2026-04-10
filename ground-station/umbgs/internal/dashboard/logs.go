package dashboard

import (
	"bufio"
	"context"
	"encoding/json"
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
	args := []string{"--follow", "--no-pager", "--output=json", "--lines=50"}
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
	scanner.Buffer(make([]byte, 0, 8192), 8192)
	for scanner.Scan() {
		entry := parseJournalJSON(scanner.Bytes())
		la.hub.BroadcastLog(entry)
	}

	return cmd.Wait()
}

// journalEntry is the subset of journalctl JSON fields we care about.
type journalEntry struct {
	SyslogIdentifier string `json:"SYSLOG_IDENTIFIER"`
	Message          string `json:"MESSAGE"`
	Priority         string `json:"PRIORITY"`
}

func parseJournalJSON(data []byte) types.LogEntry {
	entry := types.LogEntry{
		Timestamp: time.Now().UTC(),
		Service:   "system",
		Level:     "INFO",
	}

	var je journalEntry
	if err := json.Unmarshal(data, &je); err != nil {
		// Fallback: treat as plain text
		entry.Message = string(data)
		return entry
	}

	entry.Service = je.SyslogIdentifier
	if entry.Service == "" {
		entry.Service = "system"
	}
	entry.Message = je.Message

	// Map systemd priority (0=emerg..7=debug) to our levels
	switch je.Priority {
	case "0", "1", "2", "3": // emerg, alert, crit, err
		entry.Level = "ERROR"
	case "4": // warning
		entry.Level = "WARN"
	case "7": // debug
		entry.Level = "DEBUG"
	default: // 5=notice, 6=info
		entry.Level = "INFO"
	}

	// Also detect level from message content (for structured JSON logs from umbgs)
	upper := strings.ToUpper(entry.Message)
	if strings.Contains(upper, "\"LEVEL\":\"ERROR\"") || strings.Contains(upper, "\"LEVEL\":\"WARN\"") {
		if strings.Contains(upper, "ERROR") {
			entry.Level = "ERROR"
		} else if strings.Contains(upper, "WARN") {
			entry.Level = "WARN"
		}
	}

	return entry
}

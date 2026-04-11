package dashboard

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os/exec"
	"strings"
	"syscall"
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
	args := []string{"--follow", "--no-pager", "--output=json", "--lines=100"}
	for _, u := range units {
		args = append(args, "-u", u)
	}

	cmd := exec.Command("journalctl", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}

	if err := cmd.Start(); err != nil {
		return err
	}

	// Kill the process group when context is cancelled
	go func() {
		<-ctx.Done()
		if cmd.Process != nil {
			syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		}
	}()

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 16384), 16384)
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
	Timestamp        string `json:"__REALTIME_TIMESTAMP"` // microseconds since epoch
}

// slogEntry represents a structured JSON log line produced by slog.
type slogEntry struct {
	Time    string `json:"time"`
	Level   string `json:"level"`
	Msg     string `json:"msg"`
	Service string `json:"service"`
	Source  string `json:"source"` // used by logWriter (direwolf/rtl_fm output)
	Name    string `json:"name"`
	Error   string `json:"error,omitempty"`
}

func parseJournalJSON(data []byte) types.LogEntry {
	entry := types.LogEntry{
		Timestamp: time.Now().UTC(),
		Service:   "system",
		Level:     "INFO",
	}

	var je journalEntry
	if err := json.Unmarshal(data, &je); err != nil {
		entry.Message = string(data)
		return entry
	}

	// Parse journalctl timestamp (microseconds since epoch)
	if je.Timestamp != "" {
		if usec := parseInt64(je.Timestamp); usec > 0 {
			entry.Timestamp = time.Unix(usec/1e6, (usec%1e6)*1000).UTC()
		}
	}

	// Map systemd priority to level
	switch je.Priority {
	case "0", "1", "2", "3":
		entry.Level = "ERROR"
	case "4":
		entry.Level = "WARN"
	case "7":
		entry.Level = "DEBUG"
	default:
		entry.Level = "INFO"
	}

	// For non-umbgs services (gpsd, chrony, direwolf), use SYSLOG_IDENTIFIER as-is
	if je.SyslogIdentifier != "umbgs" && je.SyslogIdentifier != "" {
		entry.Service = je.SyslogIdentifier
		entry.Message = cleanMessage(je.Message)
		return entry
	}

	// umbgs logs are JSON from slog — parse the inner message to extract
	// the subsystem service name, level, and clean message text.
	var se slogEntry
	if err := json.Unmarshal([]byte(je.Message), &se); err != nil {
		// Not valid JSON — use as-is (shouldn't happen for umbgs but be safe)
		entry.Service = "umbgs"
		entry.Message = cleanMessage(je.Message)
		return entry
	}

	// Extract service/subsystem name
	entry.Service = se.Service
	if entry.Service == "" && se.Source != "" {
		// logWriter output from direwolf/rtl_fm subprocess
		entry.Service = se.Source
	}
	if entry.Service == "" && se.Name != "" {
		entry.Service = se.Name
	}
	if entry.Service == "" {
		entry.Service = "umbgs"
	}

	// Extract level from slog (overrides journalctl priority for accuracy)
	switch strings.ToUpper(se.Level) {
	case "ERROR":
		entry.Level = "ERROR"
	case "WARN":
		entry.Level = "WARN"
	case "DEBUG":
		entry.Level = "DEBUG"
	default:
		entry.Level = "INFO"
	}

	// Build clean message: "msg" + key fields (skip time/level/msg/service/source/name)
	msg := se.Msg
	if se.Error != "" {
		msg += " error=" + se.Error
	}

	// Parse remaining fields for context
	var raw map[string]interface{}
	if err := json.Unmarshal([]byte(je.Message), &raw); err == nil {
		skip := map[string]bool{"time": true, "level": true, "msg": true, "service": true, "source": true, "name": true, "error": true}
		for k, v := range raw {
			if skip[k] {
				continue
			}
			switch val := v.(type) {
			case string:
				if val != "" {
					msg += " " + k + "=" + val
				}
			case float64:
				// Format without unnecessary decimals
				if val == float64(int64(val)) {
					msg += " " + k + "=" + strings.TrimRight(strings.TrimRight(formatFloat(val), "0"), ".")
				} else {
					msg += " " + k + "=" + formatFloat(val)
				}
			case bool:
				if val {
					msg += " " + k + "=true"
				} else {
					msg += " " + k + "=false"
				}
			}
		}
	}

	entry.Message = cleanMessage(msg)
	return entry
}

// cleanMessage strips control characters and redundant whitespace.
func cleanMessage(s string) string {
	// Replace literal \n sequences and actual newlines
	s = strings.ReplaceAll(s, "\\n", " ")
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.ReplaceAll(s, "\\t", " ")
	s = strings.ReplaceAll(s, "\t", " ")
	s = strings.ReplaceAll(s, "\\r", "")
	s = strings.ReplaceAll(s, "\r", "")
	// Collapse multiple spaces
	for strings.Contains(s, "  ") {
		s = strings.ReplaceAll(s, "  ", " ")
	}
	return strings.TrimSpace(s)
}

func parseInt64(s string) int64 {
	var n int64
	for _, c := range s {
		if c >= '0' && c <= '9' {
			n = n*10 + int64(c-'0')
		}
	}
	return n
}

func formatFloat(f float64) string {
	if f == float64(int64(f)) {
		return fmt.Sprintf("%d", int64(f))
	}
	return fmt.Sprintf("%g", f)
}

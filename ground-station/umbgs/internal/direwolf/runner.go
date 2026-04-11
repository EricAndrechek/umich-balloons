package direwolf

import (
	"context"
	"fmt"
	"log/slog"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
)

// Runner manages the rtl_fm | direwolf pipeline as a subprocess.
// It replaces the standalone direwolf.service, ensuring config is
// generated before direwolf starts and restarting on failure.
type Runner struct {
	cfg    *config.Manager
	logger *slog.Logger
	mu     sync.RWMutex
	status string // "running", "stopped", "disabled", "restarting", "error"
}

// Status returns the current direwolf pipeline status.
func (r *Runner) Status() string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.status == "" {
		return "stopped"
	}
	return r.status
}

func (r *Runner) setStatus(s string) {
	r.mu.Lock()
	r.status = s
	r.mu.Unlock()
}

// NewRunner creates a direwolf pipeline runner.
func NewRunner(cfg *config.Manager, logger *slog.Logger) *Runner {
	return &Runner{cfg: cfg, logger: logger.With("service", "direwolf-runner")}
}

// Run starts the rtl_fm | direwolf pipeline and restarts on failure.
// It blocks until ctx is cancelled.
func (r *Runner) Run(ctx context.Context) error {
	for {
		c := r.cfg.Get()
		if !c.APRS.Enabled {
			r.setStatus("disabled")
			r.logger.Info("APRS disabled, direwolf runner idle")
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(30 * time.Second):
				continue
			}
		}

		// Generate direwolf.conf before each start (picks up config changes)
		if err := GenerateConfig(r.cfg, r.logger); err != nil {
			r.setStatus("error")
			r.logger.Error("failed to generate direwolf.conf", "error", err)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(15 * time.Second):
				continue
			}
		}

		r.setStatus("running")
		err := r.runPipeline(ctx, c)
		if ctx.Err() != nil {
			r.setStatus("stopped")
			return ctx.Err()
		}
		r.setStatus("restarting")

		// Detect missing hardware vs runtime errors for appropriate backoff
		backoff := 10 * time.Second
		if isNoDeviceError(err) {
			r.logger.Debug("RTL-SDR hardware not found, retrying in 60s", "error", err)
			backoff = 60 * time.Second
		} else if isDeviceBusyError(err) {
			r.logger.Warn("RTL-SDR device busy, retrying in 15s", "error", err)
			backoff = 15 * time.Second
		} else {
			r.logger.Warn("direwolf pipeline exited, restarting", "error", err, "backoff", backoff)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
	}
}

// runPipeline starts rtl_fm piped into direwolf and waits for either to exit.
func (r *Runner) runPipeline(ctx context.Context, c config.Config) error {
	freqHz := int(c.APRS.Frequency * 1e6)

	// rtl_fm flags: -f frequency, default sample rate 24k matches direwolf -r 24000.
	// Note: the rtlsdrblog fork does not support -D (decimation); use defaults.
	rtlArgs := []string{
		"-f", fmt.Sprintf("%d", freqHz),
	}
	if c.APRS.Gain > 0 {
		rtlArgs = append(rtlArgs, "-g", fmt.Sprintf("%d", c.APRS.Gain))
	}

	// direwolf flags matching proven v2025 config:
	// -n 1 (single channel) -r 24000 (sample rate) -B 1200 (baud)
	// -t 0 (no terminal colors) -c config -  (read from stdin)
	dwArgs := []string{
		"-n", "1",
		"-r", "24000",
		"-B", "1200",
		"-t", "0",
		"-c", confPath,
		"-",
	}

	r.logger.Info("starting rtl_fm | direwolf pipeline",
		"freq_hz", freqHz, "gain", c.APRS.Gain, "conf", confPath)

	rtlCmd := exec.Command("rtl_fm", rtlArgs...)
	dwCmd := exec.Command("direwolf", dwArgs...)

	// Use process groups so we can kill entire process trees on shutdown
	rtlCmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	dwCmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	// Pipe rtl_fm stdout → direwolf stdin
	pipe, err := rtlCmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("rtl_fm stdout pipe: %w", err)
	}
	dwCmd.Stdin = pipe

	// Capture stderr for error detection
	rtlStderr := &captureWriter{logger: r.logger, prefix: "rtl_fm"}
	rtlCmd.Stderr = rtlStderr

	// Send direwolf output to journal via our logger's stdout
	dwCmd.Stdout = &logWriter{logger: r.logger, prefix: "direwolf"}
	dwCmd.Stderr = &logWriter{logger: r.logger, prefix: "direwolf"}

	// Start both processes
	if err := rtlCmd.Start(); err != nil {
		return fmt.Errorf("start rtl_fm: %w", err)
	}
	if err := dwCmd.Start(); err != nil {
		killProcessGroup(rtlCmd)
		rtlCmd.Wait()
		return fmt.Errorf("start direwolf: %w", err)
	}

	// Wait for either to exit — if one dies, kill the other
	done := make(chan error, 2)
	go func() { done <- rtlCmd.Wait() }()
	go func() { done <- dwCmd.Wait() }()

	// Also watch for context cancellation to kill both immediately
	go func() {
		<-ctx.Done()
		killProcessGroup(rtlCmd)
		killProcessGroup(dwCmd)
	}()

	firstErr := <-done

	// Kill the other process group
	killProcessGroup(rtlCmd)
	killProcessGroup(dwCmd)

	// Drain the second result with a short timeout
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		r.logger.Warn("timed out waiting for second pipeline process to exit")
	}

	// Check rtl_fm stderr for hardware errors
	if stderrOut := rtlStderr.Output(); stderrOut != "" {
		if isNoDeviceMsg(stderrOut) {
			return fmt.Errorf("rtl_fm: no RTL-SDR device found")
		}
		if isDeviceBusyMsg(stderrOut) {
			return fmt.Errorf("rtl_fm: device busy")
		}
	}

	if firstErr != nil {
		return fmt.Errorf("pipeline process exited: %w", firstErr)
	}
	return fmt.Errorf("pipeline exited cleanly (unexpected)")
}

// killProcessGroup sends SIGKILL to the entire process group of cmd.
// Safe to call even if the process has already exited.
func killProcessGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	// Kill the entire process group (negative PID)
	syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
}

// Error classification for rtl_fm failures

var noDevicePatterns = []string{
	"no supported devices found",
	"no rtl-sdr",
	"usb_open error",
	"no device",
}

var deviceBusyPatterns = []string{
	"device or resource busy",
	"usb_claim_interface error",
	"device busy",
}

func isNoDeviceError(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	for _, p := range noDevicePatterns {
		if strings.Contains(msg, p) {
			return true
		}
	}
	return false
}

func isDeviceBusyError(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	for _, p := range deviceBusyPatterns {
		if strings.Contains(msg, p) {
			return true
		}
	}
	return false
}

func isNoDeviceMsg(s string) bool {
	lower := strings.ToLower(s)
	for _, p := range noDevicePatterns {
		if strings.Contains(lower, p) {
			return true
		}
	}
	return false
}

func isDeviceBusyMsg(s string) bool {
	lower := strings.ToLower(s)
	for _, p := range deviceBusyPatterns {
		if strings.Contains(lower, p) {
			return true
		}
	}
	return false
}

// logWriter adapts process output to structured logging.
type logWriter struct {
	logger *slog.Logger
	prefix string
}

func (w *logWriter) Write(p []byte) (int, error) {
	w.logger.Info(string(p), "source", w.prefix)
	return len(p), nil
}

// captureWriter logs like logWriter but also captures output for error detection.
type captureWriter struct {
	logger *slog.Logger
	prefix string
	mu     sync.Mutex
	buf    strings.Builder
}

func (w *captureWriter) Write(p []byte) (int, error) {
	w.logger.Info(string(p), "source", w.prefix)
	w.mu.Lock()
	w.buf.Write(p)
	// Keep only last 4KB to avoid unbounded growth
	if w.buf.Len() > 4096 {
		s := w.buf.String()
		w.buf.Reset()
		w.buf.WriteString(s[len(s)-2048:])
	}
	w.mu.Unlock()
	return len(p), nil
}

func (w *captureWriter) Output() string {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.buf.String()
}

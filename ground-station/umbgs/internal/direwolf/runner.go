package direwolf

import (
	"context"
	"fmt"
	"log/slog"
	"os/exec"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
)

// Runner manages the rtl_fm | direwolf pipeline as a subprocess.
// It replaces the standalone direwolf.service, ensuring config is
// generated before direwolf starts and restarting on failure.
type Runner struct {
	cfg    *config.Manager
	logger *slog.Logger
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
			r.logger.Error("failed to generate direwolf.conf", "error", err)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(15 * time.Second):
				continue
			}
		}

		err := r.runPipeline(ctx, c)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		r.logger.Warn("direwolf pipeline exited, restarting in 10s", "error", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(10 * time.Second):
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

	rtlCmd := exec.CommandContext(ctx, "rtl_fm", rtlArgs...)
	dwCmd := exec.CommandContext(ctx, "direwolf", dwArgs...)

	// Pipe rtl_fm stdout → direwolf stdin
	pipe, err := rtlCmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("rtl_fm stdout pipe: %w", err)
	}
	dwCmd.Stdin = pipe

	// Send direwolf output to journal via our logger's stdout
	dwCmd.Stdout = &logWriter{logger: r.logger, prefix: "direwolf"}
	dwCmd.Stderr = &logWriter{logger: r.logger, prefix: "direwolf"}
	rtlCmd.Stderr = &logWriter{logger: r.logger, prefix: "rtl_fm"}

	// Start both processes
	if err := rtlCmd.Start(); err != nil {
		return fmt.Errorf("start rtl_fm: %w", err)
	}
	if err := dwCmd.Start(); err != nil {
		rtlCmd.Process.Kill()
		rtlCmd.Wait()
		return fmt.Errorf("start direwolf: %w", err)
	}

	// Wait for either to exit — if one dies, kill the other
	done := make(chan error, 2)
	go func() { done <- rtlCmd.Wait() }()
	go func() { done <- dwCmd.Wait() }()

	firstErr := <-done

	// Kill the other process
	if rtlCmd.ProcessState == nil {
		rtlCmd.Process.Kill()
	}
	if dwCmd.ProcessState == nil {
		dwCmd.Process.Kill()
	}

	// Drain the second result
	select {
	case <-done:
	case <-time.After(5 * time.Second):
	}

	if firstErr != nil {
		return fmt.Errorf("pipeline process exited: %w", firstErr)
	}
	return fmt.Errorf("pipeline exited cleanly (unexpected)")
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

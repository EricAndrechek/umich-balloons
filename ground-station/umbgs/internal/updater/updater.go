// Package updater implements A/B slot binary updates with rollback.
package updater

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
)

const (
	slotA       = "/data/umbgs-a"
	slotB       = "/data/umbgs-b"
	activeFile  = "/data/active"  // contains "a" or "b"
	pendingFile = "/data/pending" // created during update, removed after healthy boot
)

// Updater checks for and applies binary updates.
type Updater struct {
	cfg     *config.Manager
	version string
	logger  *slog.Logger
}

// NewUpdater creates an updater.
func NewUpdater(cfg *config.Manager, version string, logger *slog.Logger) *Updater {
	return &Updater{cfg: cfg, version: version, logger: logger.With("service", "updater")}
}

// MarkHealthy removes the pending file, confirming the current version is stable.
// Called after successful startup.
func MarkHealthy() error {
	return os.Remove(pendingFile)
}

// ActiveSlot returns the current active slot ("a" or "b").
func ActiveSlot() string {
	data, err := os.ReadFile(activeFile)
	if err != nil {
		return "a"
	}
	s := strings.TrimSpace(string(data))
	if s == "b" {
		return "b"
	}
	return "a"
}

// InactiveSlot returns the slot not currently active.
func InactiveSlot() string {
	if ActiveSlot() == "a" {
		return "b"
	}
	return "a"
}

func slotPath(slot string) string {
	if slot == "b" {
		return slotB
	}
	return slotA
}

// Run marks the current boot as healthy, then blocks until context is cancelled.
// Updates are triggered manually via the Check method (called from the dashboard).
func (u *Updater) Run(ctx context.Context) error {
	c := u.cfg.Get()
	if !c.Update.Enabled {
		u.logger.Info("updates disabled")
		<-ctx.Done()
		return ctx.Err()
	}

	// Mark current boot as healthy after startup
	if err := MarkHealthy(); err != nil {
		u.logger.Debug("no pending file to clear", "error", err)
	}

	u.logger.Info("updater ready, waiting for manual trigger")
	<-ctx.Done()
	return ctx.Err()
}

// Check checks for an available update and applies it if found.
// This is called manually from the dashboard API.
func (u *Updater) Check(ctx context.Context) error {
	return u.check(ctx)
}

func (u *Updater) check(ctx context.Context) error {
	c := u.cfg.Get()
	channel := c.Update.Channel
	if channel == "" {
		channel = "stable"
	}

	checkURL := fmt.Sprintf("%s/update/check?version=%s&channel=%s", c.APIUrl, u.version, channel)
	u.logger.Info("checking for updates", "url", checkURL)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, checkURL, nil)
	if err != nil {
		u.logger.Error("failed to create update request", "error", err)
		return fmt.Errorf("create request: %w", err)
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		u.logger.Warn("update check failed", "error", err)
		return fmt.Errorf("check failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNoContent || resp.StatusCode == http.StatusNotFound {
		u.logger.Debug("no update available")
		return nil
	}

	// 405 = endpoint doesn't exist yet on server; treat as "no update available"
	if resp.StatusCode == http.StatusMethodNotAllowed {
		u.logger.Debug("update endpoint not available on server (405)")
		return nil
	}

	if resp.StatusCode != http.StatusOK {
		u.logger.Warn("unexpected update response", "status", resp.StatusCode)
		return fmt.Errorf("unexpected status: %d", resp.StatusCode)
	}

	// Response contains the binary directly
	expectedHash := resp.Header.Get("X-SHA256")
	if expectedHash == "" {
		u.logger.Error("update response missing X-SHA256 header")
		return fmt.Errorf("missing X-SHA256 header")
	}

	if err := u.apply(resp.Body, expectedHash); err != nil {
		u.logger.Error("failed to apply update", "error", err)
		return fmt.Errorf("apply: %w", err)
	}

	u.logger.Info("update applied, will take effect on next restart")
	return nil
}

func (u *Updater) apply(body io.Reader, expectedHash string) error {
	target := slotPath(InactiveSlot())
	tmpPath := target + ".tmp"

	f, err := os.Create(tmpPath)
	if err != nil {
		return fmt.Errorf("create temp: %w", err)
	}

	hasher := sha256.New()
	w := io.MultiWriter(f, hasher)

	if _, err := io.Copy(w, body); err != nil {
		f.Close()
		os.Remove(tmpPath)
		return fmt.Errorf("download: %w", err)
	}
	f.Close()

	// Verify hash
	actualHash := hex.EncodeToString(hasher.Sum(nil))
	if actualHash != expectedHash {
		os.Remove(tmpPath)
		return fmt.Errorf("hash mismatch: expected %s, got %s", expectedHash, actualHash)
	}

	// Make executable
	if err := os.Chmod(tmpPath, 0755); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("chmod: %w", err)
	}

	// Atomic rename
	if err := os.Rename(tmpPath, target); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("rename: %w", err)
	}

	// Write pending file (watchdog will check this)
	pending := InactiveSlot()
	if err := os.WriteFile(pendingFile, []byte(pending), 0644); err != nil {
		return fmt.Errorf("write pending: %w", err)
	}

	// Switch active slot
	if err := os.WriteFile(activeFile, []byte(pending), 0644); err != nil {
		return fmt.Errorf("write active: %w", err)
	}

	// Create symlink for systemd
	symlink := filepath.Join("/data", "umbgs")
	os.Remove(symlink)
	os.Symlink(target, symlink)

	return nil
}

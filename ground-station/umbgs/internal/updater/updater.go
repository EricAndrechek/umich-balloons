// Package updater implements A/B slot binary updates with rollback.
//
// Updates are pulled from GitHub Releases. The release assets must follow the
// naming convention produced by .github/workflows/ground-station.yml:
//
//	umbgs-linux-arm64         — the binary itself
//	umbgs-linux-arm64.sha256  — standard sha256sum output, "<hex>  <name>\n"
//
// The updater compares the running binary's version string (set at build time
// via -X main.version=${GITHUB_REF_NAME}) against the release's tag_name. Any
// difference triggers a download. No semver ordering — if the tags differ we
// update, which correctly handles dev→release, release→release, and accidental
// downgrades during a rollback.
package updater

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
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
	symlinkPath = "/data/umbgs"   // systemd ExecStart target, pointed at the active slot

	// defaultGitHubAPI is the GitHub REST API base. Overridden in tests.
	defaultGitHubAPI = "https://api.github.com"

	// defaultRepo is the source repo for releases. Hardcoded rather than
	// configurable because the dashboard config editor currently doesn't
	// preserve unknown keys, and this is a single-project binary. Forks
	// that need a different source can change this constant at build time.
	defaultRepo = "EricAndrechek/umich-balloons"

	// Asset names must match those published by the release workflow.
	assetBinary = "umbgs-linux-arm64"
	assetSHA    = "umbgs-linux-arm64.sha256"

	// defaultHealthyDelay is how long the new binary must run before we
	// clear the pending marker. Must be meaningfully shorter than the
	// watchdog's MAX_PENDING_AGE (10 minutes in watchdog.sh) so that a
	// healthy boot clears the marker well before the watchdog would roll
	// back, but long enough that a crash-looping binary never reaches it.
	//
	// 3 minutes is the sweet spot: direwolf has typically connected to
	// APRS-IS, gpsd has handed off a fix, the LoRa reader has opened its
	// port, and the uploader has attempted at least one buffered packet.
	// A binary that crashes within 3 minutes of every boot will leave the
	// pending file untouched, and at T+10min the watchdog will roll back.
	defaultHealthyDelay = 3 * time.Minute
)

// Updater checks for and applies binary updates.
type Updater struct {
	cfg     *config.Manager
	version string
	logger  *slog.Logger

	// Fields below are overridable for tests. Production code leaves them
	// at the defaults set by NewUpdater.
	githubAPI    string
	repo         string
	slotAPath    string
	slotBPath    string
	activePath   string
	pendingPath  string
	symlink      string
	httpClient   *http.Client
	healthyDelay time.Duration
}

// NewUpdater creates an updater.
func NewUpdater(cfg *config.Manager, version string, logger *slog.Logger) *Updater {
	return &Updater{
		cfg:         cfg,
		version:     version,
		logger:      logger.With("service", "updater"),
		githubAPI:   defaultGitHubAPI,
		repo:        defaultRepo,
		slotAPath:   slotA,
		slotBPath:   slotB,
		activePath:  activeFile,
		pendingPath: pendingFile,
		symlink:     symlinkPath,
		// Generous timeout: the binary is ~22MB and a chase vehicle on
		// a spotty cellular uplink needs some slack. The caller's ctx
		// is still respected for cancellation.
		httpClient:   &http.Client{Timeout: 5 * time.Minute},
		healthyDelay: defaultHealthyDelay,
	}
}

// markHealthy removes the pending file, confirming the current version is stable.
// Called from Run() after the boot-health delay has elapsed without the
// process dying.
func (u *Updater) markHealthy() error {
	err := os.Remove(u.pendingPath)
	if err != nil && os.IsNotExist(err) {
		// No pending file is the normal case on a boot that wasn't
		// preceded by an update. Not an error.
		return nil
	}
	return err
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

// inactiveSlot is the method form used inside Check so tests can override
// the active/inactive file paths.
func (u *Updater) inactiveSlot() string {
	data, err := os.ReadFile(u.activePath)
	active := "a"
	if err == nil && strings.TrimSpace(string(data)) == "b" {
		active = "b"
	}
	if active == "a" {
		return "b"
	}
	return "a"
}

func (u *Updater) slotPath(slot string) string {
	if slot == "b" {
		return u.slotBPath
	}
	return u.slotAPath
}

// Run blocks until context is cancelled. After a delay (healthyDelay), it
// clears the pending marker file to signal to the watchdog that this boot
// is stable. Updates themselves are triggered manually via Check (called
// from the dashboard), not from Run.
//
// The delayed mark-healthy is load-bearing: if Run cleared the pending
// marker immediately at startup (the pre-2026-04 behavior), a new binary
// that crashes 30 seconds into its first boot would leave no pending
// marker, and the watchdog's rollback check (see watchdog.sh) would never
// fire. By holding off on the clear until the process has survived
// healthyDelay of continuous runtime, a crash-looping binary is guaranteed
// to leave the marker in place until the watchdog catches it.
func (u *Updater) Run(ctx context.Context) error {
	c := u.cfg.Get()
	if !c.Update.Enabled {
		u.logger.Info("updates disabled")
		<-ctx.Done()
		return ctx.Err()
	}

	u.logger.Info("updater started, waiting to confirm boot health",
		"healthy_delay", u.healthyDelay,
		"repo", u.repo, "channel", c.Update.Channel, "current", u.version)

	// Wait for healthyDelay or ctx cancellation. If ctx fires first, we
	// deliberately do NOT clear the pending marker — this boot didn't run
	// long enough to prove itself stable, so leave the marker for the
	// watchdog to observe.
	timer := time.NewTimer(u.healthyDelay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
	}

	if err := u.markHealthy(); err != nil {
		u.logger.Warn("failed to clear pending marker", "error", err)
	} else {
		u.logger.Info("boot confirmed healthy, pending marker cleared")
	}

	u.logger.Info("updater ready, waiting for manual trigger")
	<-ctx.Done()
	return ctx.Err()
}

// Result describes the outcome of a Check call. The dashboard renders
// Message verbatim so it must be human-readable.
type Result struct {
	Updated        bool   `json:"updated"`
	CurrentVersion string `json:"current_version"`
	LatestVersion  string `json:"latest_version,omitempty"`
	Message        string `json:"message"`
}

// Check queries GitHub Releases and applies an update if one is available.
// Returns a Result describing what happened; the dashboard surfaces
// Result.Message to the user. An error is returned only for failures that
// should be treated as such by the caller (network errors, corrupt downloads,
// filesystem errors). "No release yet" and "already up to date" are normal
// outcomes and return Result with nil error.
func (u *Updater) Check(ctx context.Context) (Result, error) {
	c := u.cfg.Get()
	channel := c.Update.Channel
	if channel == "" {
		channel = "stable"
	}

	result := Result{CurrentVersion: u.version}
	u.logger.Info("checking for updates",
		"repo", u.repo, "channel", channel, "current", u.version)

	release, err := u.fetchRelease(ctx, channel)
	if err != nil {
		return result, fmt.Errorf("fetch release metadata: %w", err)
	}
	if release == nil {
		result.Message = "no releases published yet"
		u.logger.Info("no releases available")
		return result, nil
	}

	result.LatestVersion = release.TagName

	// Tag match → we're already on it. Simple string equality so that dev
	// builds ("dev") always see an update as available, which is the
	// behavior we want on a freshly-flashed card.
	if release.TagName == u.version {
		result.Message = "already up to date (" + u.version + ")"
		u.logger.Info("already up to date", "version", u.version)
		return result, nil
	}

	u.logger.Info("update available",
		"current", u.version, "latest", release.TagName)

	// Locate the required assets by name.
	var binURL, shaURL string
	for _, a := range release.Assets {
		switch a.Name {
		case assetBinary:
			binURL = a.BrowserDownloadURL
		case assetSHA:
			shaURL = a.BrowserDownloadURL
		}
	}
	if binURL == "" || shaURL == "" {
		return result, fmt.Errorf("release %s is missing required assets (need %s and %s)",
			release.TagName, assetBinary, assetSHA)
	}

	expectedHash, err := u.fetchSHA256(ctx, shaURL)
	if err != nil {
		return result, fmt.Errorf("fetch sha256: %w", err)
	}

	if err := u.downloadAndApply(ctx, binURL, expectedHash); err != nil {
		return result, fmt.Errorf("apply: %w", err)
	}

	result.Updated = true
	result.Message = "updated to " + release.TagName + " — restart required"
	u.logger.Info("update applied, will take effect on next restart",
		"version", release.TagName)
	return result, nil
}

// githubRelease mirrors the subset of the GitHub release JSON we care about.
// See https://docs.github.com/en/rest/releases/releases#get-the-latest-release.
type githubRelease struct {
	TagName    string         `json:"tag_name"`
	Draft      bool           `json:"draft"`
	Prerelease bool           `json:"prerelease"`
	Assets     []releaseAsset `json:"assets"`
}

type releaseAsset struct {
	Name               string `json:"name"`
	BrowserDownloadURL string `json:"browser_download_url"`
	Size               int64  `json:"size"`
}

// fetchRelease returns the latest release for the channel, or (nil, nil) if
// the repo has no releases yet (a normal state for a freshly-cut project).
//
// channel semantics:
//   - "stable" (default): /releases/latest — GitHub excludes drafts and
//     prereleases from this endpoint automatically.
//   - "beta": list recent releases, return the newest non-draft (which may
//     be a prerelease).
func (u *Updater) fetchRelease(ctx context.Context, channel string) (*githubRelease, error) {
	var url string
	switch strings.ToLower(channel) {
	case "", "stable":
		url = fmt.Sprintf("%s/repos/%s/releases/latest", u.githubAPI, u.repo)
	case "beta", "prerelease":
		url = fmt.Sprintf("%s/repos/%s/releases?per_page=10", u.githubAPI, u.repo)
	default:
		return nil, fmt.Errorf("unknown update channel %q (expected 'stable' or 'beta')", channel)
	}

	resp, err := u.get(ctx, url, "application/vnd.github+json")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	// 404 on /releases/latest means "no releases yet" — expected for a
	// brand-new repo. Treat it as "nothing to do" rather than an error so
	// the dashboard doesn't flash red the first time an operator checks.
	if resp.StatusCode == http.StatusNotFound {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, fmt.Errorf("GitHub API returned HTTP %d: %s",
			resp.StatusCode, strings.TrimSpace(string(body)))
	}

	isBeta := strings.EqualFold(channel, "beta") || strings.EqualFold(channel, "prerelease")
	if isBeta {
		var releases []githubRelease
		if err := json.NewDecoder(resp.Body).Decode(&releases); err != nil {
			return nil, fmt.Errorf("decode releases list: %w", err)
		}
		for i := range releases {
			if releases[i].Draft {
				continue
			}
			// GitHub returns newest-first, so first non-draft wins.
			return &releases[i], nil
		}
		return nil, nil
	}

	var release githubRelease
	if err := json.NewDecoder(resp.Body).Decode(&release); err != nil {
		return nil, fmt.Errorf("decode release: %w", err)
	}
	return &release, nil
}

// fetchSHA256 downloads a sha256sum-format file and returns the lowercase
// hex hash. The expected format is the standard `sha256sum` output:
// "<64-char hex>  <filename>\n". We only read the first field.
func (u *Updater) fetchSHA256(ctx context.Context, url string) (string, error) {
	resp, err := u.get(ctx, url, "")
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("HTTP %d fetching sha256", resp.StatusCode)
	}
	// 1 KiB cap: legitimate sha256sum files are <100 bytes. Anything
	// larger is a sign something is very wrong (proxy injecting HTML,
	// wrong asset served, etc.) and we'd rather bail than risk parsing it.
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1024))
	if err != nil {
		return "", err
	}
	fields := strings.Fields(strings.TrimSpace(string(body)))
	if len(fields) == 0 {
		return "", fmt.Errorf("empty sha256 file")
	}
	hash := strings.ToLower(fields[0])
	if len(hash) != 64 {
		return "", fmt.Errorf("expected 64-char sha256 hex, got %d chars", len(hash))
	}
	return hash, nil
}

// downloadAndApply streams the binary from url into the inactive slot,
// verifies the hash as it writes, then atomically swaps the active slot.
func (u *Updater) downloadAndApply(ctx context.Context, url, expectedHash string) error {
	resp, err := u.get(ctx, url, "")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d downloading binary", resp.StatusCode)
	}
	return u.apply(resp.Body, expectedHash)
}

// get performs a GET with the headers GitHub expects.
func (u *Updater) get(ctx context.Context, url, accept string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	// GitHub requires User-Agent and rate-limits anonymous callers by it.
	req.Header.Set("User-Agent", "umbgs/"+u.version)
	if accept != "" {
		req.Header.Set("Accept", accept)
		req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	}
	return u.httpClient.Do(req)
}

// apply writes body to the inactive slot, verifies it matches expectedHash,
// then swaps the active pointer and symlink atomically.
func (u *Updater) apply(body io.Reader, expectedHash string) error {
	target := u.slotPath(u.inactiveSlot())
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
	if err := f.Close(); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("close temp: %w", err)
	}

	actualHash := hex.EncodeToString(hasher.Sum(nil))
	if actualHash != expectedHash {
		os.Remove(tmpPath)
		return fmt.Errorf("hash mismatch: expected %s, got %s", expectedHash, actualHash)
	}

	if err := os.Chmod(tmpPath, 0755); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("chmod: %w", err)
	}

	if err := os.Rename(tmpPath, target); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("rename: %w", err)
	}

	// Write pending file (watchdog will check this)
	pending := u.inactiveSlot()
	if err := os.WriteFile(u.pendingPath, []byte(pending), 0644); err != nil {
		return fmt.Errorf("write pending: %w", err)
	}

	// Switch active slot
	if err := os.WriteFile(u.activePath, []byte(pending), 0644); err != nil {
		return fmt.Errorf("write active: %w", err)
	}

	// Swap the symlink that systemd's ExecStart points at.
	os.Remove(u.symlink)
	if dir := filepath.Dir(u.symlink); dir != "." && dir != "" {
		// Best-effort mkdir for test runs; production the dir exists.
		_ = os.MkdirAll(dir, 0755)
	}
	if err := os.Symlink(target, u.symlink); err != nil {
		// Non-fatal: if we can't create the symlink (e.g., on the test
		// tmpfs or a stale /data), the active file above is still the
		// source of truth for the next boot.
		u.logger.Warn("symlink update failed", "error", err)
	}

	return nil
}

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
//
// Check vs Apply: Check only fetches release metadata and returns whether an
// update is available. Apply does the actual download + hash verify + slot
// swap. This split exists so the dashboard can offer a "review then install"
// UX and so that a long download on a marginal uplink never blocks an HTTP
// request handler. Apply is expected to run in a goroutine with the updater's
// State() polled for progress.
package updater

import (
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
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
	//
	// assetBinaryGZ is preferred over assetBinary when both are present:
	// gzip -9 on a Go binary typically gives ~60% reduction, which is a
	// meaningful bandwidth win on a chase vehicle's one-bar LTE uplink.
	// The updater streams the download through gzip.NewReader so the
	// decompressed content is what hits the hash pipeline AND the slot
	// file — i.e., we verify what's actually installed, not the transport
	// blob. assetBinary is kept as a fallback for releases that predate
	// the .gz publication or that are cut by hand without it.
	assetBinary   = "umbgs-linux-arm64"
	assetBinaryGZ = "umbgs-linux-arm64.gz"
	assetSHA      = "umbgs-linux-arm64.sha256"

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

	// progressInterval throttles progress reports to keep stateMu from
	// being hammered during a fast download. 200ms is fast enough to feel
	// live on the dashboard while keeping overhead negligible.
	progressInterval = 200 * time.Millisecond
)

// ErrApplyInProgress is returned by Apply when another Apply call is already
// running. Callers that want to join an in-progress update should poll
// State() instead of retrying Apply.
var ErrApplyInProgress = errors.New("update already in progress")

// Phase is the updater's current high-level activity. Clients render their
// UI from this (spinner, progress bar, restart prompt) rather than parsing
// Message strings.
type Phase string

const (
	PhaseIdle        Phase = "idle"        // nothing happening
	PhaseChecking    Phase = "checking"    // fetching metadata
	PhaseDownloading Phase = "downloading" // streaming the binary
	PhaseVerifying   Phase = "verifying"   // hash check + slot swap
	PhaseApplied     Phase = "applied"     // installed, restart required
	PhaseError       Phase = "error"       // last attempt failed
)

// State is the snapshot returned by State(). Safe to marshal as JSON.
//
// Available + LastCheckedAt persist the most recent Check() result so the
// dashboard can offer an "Install" button on page reload without forcing
// the operator to hit Check again. They live alongside the Apply progress
// fields because they're all read by the same /api/update/status poll.
type State struct {
	Phase          Phase     `json:"phase"`
	Message        string    `json:"message"`
	CurrentVersion string    `json:"current_version"`
	LatestVersion  string    `json:"latest_version,omitempty"`
	Downloaded     int64     `json:"downloaded"`
	Total          int64     `json:"total"`
	UpdatedAt      time.Time `json:"updated_at"`
	// Available is true if the most recent Check() found an installable
	// update. Survives until the next Check() overwrites it or an Apply
	// succeeds (which clears it by transitioning to PhaseApplied).
	Available     bool      `json:"available"`
	LastCheckedAt time.Time `json:"last_checked_at"`
}

// CheckResult is the synchronous answer to "is there an update available?"
// Available is true only when a newer tag exists AND the required assets
// are present on the release — so the dashboard can safely offer an
// "Install" button on Available=true without risking a mid-download failure
// due to a broken release.
type CheckResult struct {
	Available      bool   `json:"available"`
	CurrentVersion string `json:"current_version"`
	LatestVersion  string `json:"latest_version,omitempty"`
	Message        string `json:"message"`
}

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

	// stateMu guards state. Read in State(), written via setState().
	stateMu sync.RWMutex
	state   State

	// applyMu is held for the duration of an Apply call so that concurrent
	// callers (e.g., two dashboards clicking Install at the same time) get
	// ErrApplyInProgress instead of racing each other through the download
	// and slot swap. TryLock is used so we reject immediately rather than
	// queuing.
	applyMu sync.Mutex
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
		// No http.Client.Timeout: a 22 MB binary over a one-bar LTE link
		// can legitimately take 10+ minutes. Cancellation is driven by the
		// ctx passed to Check/Apply, so callers can still abort.
		httpClient:   &http.Client{},
		healthyDelay: defaultHealthyDelay,
		state: State{
			Phase:          PhaseIdle,
			CurrentVersion: version,
			UpdatedAt:      time.Now(),
		},
	}
}

// State returns a snapshot of the updater's current state. Safe to call
// concurrently with Check/Apply.
func (u *Updater) State() State {
	u.stateMu.RLock()
	defer u.stateMu.RUnlock()
	return u.state
}

// setState updates the state under lock. Callers mutate the state via the
// callback so that all changes to UpdatedAt happen in one place.
func (u *Updater) setState(fn func(s *State)) {
	u.stateMu.Lock()
	defer u.stateMu.Unlock()
	fn(&u.state)
	u.state.UpdatedAt = time.Now()
}

// failState is a convenience that transitions to PhaseError with the given
// message. Used by Apply when any stage fails.
func (u *Updater) failState(msg string) {
	u.setState(func(s *State) {
		s.Phase = PhaseError
		s.Message = msg
	})
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

// inactiveSlot is the method form used inside Apply so tests can override
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
// is stable. Updates themselves are triggered manually via Check/Apply
// (called from the dashboard), not from Run.
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

	// Opportunistic auto-check: run one Check right after the boot is
	// confirmed healthy so the dashboard can show "update available"
	// without waiting for an operator to hit the button. Failures are
	// non-fatal (no network, GitHub rate-limited, etc.) — the manual
	// Check button is still available.
	go func() {
		checkCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		if _, err := u.Check(checkCtx); err != nil {
			u.logger.Debug("startup update check failed", "error", err)
		}
	}()

	u.logger.Info("updater ready, waiting for manual trigger")
	<-ctx.Done()
	return ctx.Err()
}

// Check queries GitHub Releases for the latest release and reports whether
// an update is available. It never downloads anything — call Apply to
// actually install. Returning (CheckResult, nil) with Available=false is a
// normal outcome ("already up to date", "no releases yet"); errors are
// returned only for unexpected failures (network, auth, malformed asset
// list).
//
// Check is cheap (~1 HTTP request) and safe to call from a synchronous
// HTTP handler. It also verifies that the release has the expected assets
// so that the dashboard never offers an "Install" button for a broken
// release.
//
// Check writes its result into State so it persists across dashboard
// page reloads (so a refresh after "update available" still shows the
// Install button without forcing another round-trip to GitHub). The
// write is narrowly scoped — Check only touches Available, LatestVersion
// and LastCheckedAt, and only when Phase is idle/error/applied. While
// an Apply is actively downloading/verifying we skip the state write so
// a concurrent Check from another tab can't reset the progress bar.
func (u *Updater) Check(ctx context.Context) (CheckResult, error) {
	c := u.cfg.Get()
	channel := c.Update.Channel
	if channel == "" {
		channel = "stable"
	}

	result := CheckResult{CurrentVersion: u.version}
	u.logger.Info("checking for updates",
		"repo", u.repo, "channel", channel, "current", u.version)

	release, err := u.fetchRelease(ctx, channel)
	if err != nil {
		return result, fmt.Errorf("fetch release metadata: %w", err)
	}
	if release == nil {
		result.Message = "No releases published yet"
		u.logger.Info("no releases available")
		u.recordCheckResult(result)
		return result, nil
	}

	result.LatestVersion = release.TagName

	// Tag match → we're already on it. Simple string equality so that dev
	// builds ("dev") always see an update as available, which is the
	// behavior we want on a freshly-flashed card.
	if release.TagName == u.version {
		result.Message = "Already up to date (" + u.version + ")"
		u.logger.Info("already up to date", "version", u.version)
		u.recordCheckResult(result)
		return result, nil
	}

	// Verify required assets exist before declaring the update available.
	// We don't want the user to click "Install" on something that will
	// fail at download — catch it here while Check is still synchronous.
	if _, ok := selectAssets(release); !ok {
		return result, fmt.Errorf("release %s is missing required assets (need %s or %s, plus %s)",
			release.TagName, assetBinaryGZ, assetBinary, assetSHA)
	}

	result.Available = true
	result.Message = "Update available: " + release.TagName
	u.logger.Info("update available",
		"current", u.version, "latest", release.TagName)
	u.recordCheckResult(result)
	return result, nil
}

// recordCheckResult folds the latest Check outcome into State so the
// dashboard can show the Install button across page reloads without
// re-calling Check. It is deliberately narrow: it only writes Available,
// LatestVersion and LastCheckedAt, and only when Phase is idle (i.e. no
// Apply is in progress). That way two tabs clicking Check don't race with
// an Apply's progress bar, and a successful Apply's PhaseApplied state
// isn't reverted by a stale Check write.
func (u *Updater) recordCheckResult(r CheckResult) {
	u.setState(func(s *State) {
		// Empty phase = zero-value Updater (happens in tests that
		// construct the struct directly instead of going through
		// NewUpdater). Treat it as idle so the first Check after
		// construction still persists.
		if s.Phase != "" && s.Phase != PhaseIdle && s.Phase != PhaseError && s.Phase != PhaseApplied {
			return
		}
		s.Available = r.Available
		s.LatestVersion = r.LatestVersion
		s.LastCheckedAt = time.Now()
	})
}

// Apply downloads the latest release and installs it into the inactive
// slot. It is long-running (minutes on a slow uplink) and should be called
// from a goroutine if the caller needs non-blocking UX — the dashboard
// kicks this off from its /api/update/apply handler and polls /status.
//
// Concurrent Apply calls are rejected with ErrApplyInProgress so the first
// caller wins and subsequent clicks don't race through the slot swap.
//
// Progress is published via State(); there is no streaming response.
// Cancellation is driven by ctx — callers that want to abort should cancel
// the ctx they passed in.
func (u *Updater) Apply(ctx context.Context) error {
	if !u.applyMu.TryLock() {
		return ErrApplyInProgress
	}
	defer u.applyMu.Unlock()

	c := u.cfg.Get()
	channel := c.Update.Channel
	if channel == "" {
		channel = "stable"
	}

	u.setState(func(s *State) {
		s.Phase = PhaseChecking
		s.Message = "Fetching release metadata..."
		s.CurrentVersion = u.version
		s.Downloaded = 0
		s.Total = 0
	})
	u.logger.Info("applying update", "repo", u.repo, "channel", channel)

	release, err := u.fetchRelease(ctx, channel)
	if err != nil {
		u.failState("Fetch release: " + err.Error())
		return fmt.Errorf("fetch release metadata: %w", err)
	}
	if release == nil {
		u.failState("No releases available")
		return errors.New("no releases available")
	}
	if release.TagName == u.version {
		u.setState(func(s *State) {
			s.Phase = PhaseIdle
			s.Message = "Already up to date (" + u.version + ")"
			s.LatestVersion = release.TagName
		})
		return nil
	}

	sel, ok := selectAssets(release)
	if !ok {
		msg := fmt.Sprintf("release %s is missing required assets (need %s or %s, plus %s)",
			release.TagName, assetBinaryGZ, assetBinary, assetSHA)
		u.failState(msg)
		return errors.New(msg)
	}

	u.setState(func(s *State) {
		s.LatestVersion = release.TagName
		s.Message = "Fetching checksum..."
	})

	expectedHash, err := u.fetchSHA256(ctx, sel.shaURL)
	if err != nil {
		u.failState("Fetch sha256: " + err.Error())
		return fmt.Errorf("fetch sha256: %w", err)
	}

	dlMsg := "Downloading " + release.TagName + "..."
	if sel.gzipped {
		dlMsg = "Downloading " + release.TagName + " (compressed)..."
	}
	u.setState(func(s *State) {
		s.Phase = PhaseDownloading
		s.Message = dlMsg
		s.Total = sel.binSize
		s.Downloaded = 0
	})

	if err := u.downloadAndApply(ctx, sel.binURL, expectedHash, sel.gzipped); err != nil {
		u.failState("Install failed: " + err.Error())
		return fmt.Errorf("apply: %w", err)
	}

	u.setState(func(s *State) {
		s.Phase = PhaseApplied
		s.Message = "Installed " + release.TagName + " — restart required"
		// Clear Available: we just installed it, so the "offer an
		// Install button" signal no longer applies. Next Check after
		// reboot will re-populate if a newer release exists.
		s.Available = false
	})
	u.logger.Info("update applied, will take effect on next restart",
		"version", release.TagName)
	return nil
}

// assetSelection is the outcome of picking the right download for a
// release. Prefer the gzipped asset when present; fall back to the raw
// binary. The sha256 hash always describes the DECOMPRESSED binary, so
// hash verification lives on the decompressed side of the pipeline
// regardless of which asset we downloaded.
type assetSelection struct {
	binURL   string
	binSize  int64
	shaURL   string
	gzipped  bool
}

// selectAssets picks the best download and checksum asset from a release.
// Returns ok=false if the release doesn't have enough assets to install —
// Check uses this to suppress the Install button for broken releases.
func selectAssets(release *githubRelease) (assetSelection, bool) {
	var sel assetSelection
	assets := make(map[string]releaseAsset, len(release.Assets))
	for _, a := range release.Assets {
		assets[a.Name] = a
	}
	if a, ok := assets[assetBinaryGZ]; ok {
		sel.binURL = a.BrowserDownloadURL
		sel.binSize = a.Size
		sel.gzipped = true
	} else if a, ok := assets[assetBinary]; ok {
		sel.binURL = a.BrowserDownloadURL
		sel.binSize = a.Size
	}
	if a, ok := assets[assetSHA]; ok {
		sel.shaURL = a.BrowserDownloadURL
	}
	return sel, sel.binURL != "" && sel.shaURL != ""
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
// Download progress is reported via setState through a progressReader.
//
// When gzipped=true, the url points at a .gz asset and we wrap the body
// with gzip.NewReader before passing it to apply(). Important invariant:
// the progressReader sits BELOW gzip.Reader in the stream, so progress
// counts COMPRESSED bytes (matching Content-Length of the .gz), while the
// hasher+file in apply() see DECOMPRESSED bytes (matching the published
// sha256). That way the progress bar tracks what's actually on the wire
// and the hash check proves the installed binary is intact.
//
// A corrupt .gz surfaces as an io.Copy error (gzip.Reader reports CRC
// failures on the final Read that hits EOF), which apply() turns into a
// tmp-file cleanup and a returned error — the slot swap never happens.
func (u *Updater) downloadAndApply(ctx context.Context, url, expectedHash string, gzipped bool) error {
	resp, err := u.get(ctx, url, "")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d downloading binary", resp.StatusCode)
	}

	// Prefer Content-Length if the server provided it; otherwise keep
	// whatever size the release metadata told us. Some mirrors/proxies
	// drop the header, and chunked transfer encoding omits it entirely.
	if resp.ContentLength > 0 {
		u.setState(func(s *State) { s.Total = resp.ContentLength })
	}

	var reader io.Reader = &progressReader{
		r: resp.Body,
		onProgress: func(n int64) {
			u.setState(func(s *State) { s.Downloaded = n })
		},
	}

	if gzipped {
		gr, err := gzip.NewReader(reader)
		if err != nil {
			return fmt.Errorf("gzip header: %w", err)
		}
		defer gr.Close()
		reader = gr
	}

	return u.apply(reader, expectedHash)
}

// progressReader wraps an io.Reader and reports cumulative bytes read via
// onProgress. Reports are throttled to progressInterval so the state mutex
// isn't hammered during a fast download. The final read (io.EOF) always
// flushes so the dashboard sees a 100% value before PhaseApplied.
type progressReader struct {
	r          io.Reader
	onProgress func(int64)
	read       int64
	lastReport time.Time
}

func (p *progressReader) Read(b []byte) (int, error) {
	n, err := p.r.Read(b)
	if n > 0 {
		p.read += int64(n)
	}
	// Report on any error (including EOF) so the final total always lands
	// before io.Copy returns, or on any read that's far enough past the
	// last report. This covers the common case where http.Body returns
	// (n, nil) repeatedly followed by (0, EOF) — without the err check we'd
	// miss the final total on a fast download.
	now := time.Now()
	if err != nil || now.Sub(p.lastReport) >= progressInterval {
		p.lastReport = now
		p.onProgress(p.read)
	}
	return n, err
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

	// Download done, now verify + swap — cheap but distinct phase so the
	// dashboard can surface it.
	u.setState(func(s *State) {
		s.Phase = PhaseVerifying
		s.Message = "Verifying..."
	})

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

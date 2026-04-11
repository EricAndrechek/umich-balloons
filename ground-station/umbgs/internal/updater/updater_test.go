package updater

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
)

// newTestUpdater wires up an Updater with all filesystem paths redirected
// into a temp dir and the GitHub API base URL pointed at apiURL. This lets
// us exercise the full Check() → downloadAndApply() path without touching
// /data or the network.
func newTestUpdater(t *testing.T, apiURL string) (*Updater, string) {
	t.Helper()
	dir := t.TempDir()

	cfg := config.Defaults()
	cfg.Update.Enabled = true
	cfg.Update.Channel = "stable"
	cfgMgr := config.NewManager(&cfg, "")

	u := &Updater{
		cfg:          cfgMgr,
		version:      "v0.0.1", // "current" version under test
		logger:       slog.New(slog.NewTextHandler(io.Discard, nil)),
		githubAPI:    apiURL,
		repo:         "test-owner/test-repo",
		slotAPath:    filepath.Join(dir, "umbgs-a"),
		slotBPath:    filepath.Join(dir, "umbgs-b"),
		activePath:   filepath.Join(dir, "active"),
		pendingPath:  filepath.Join(dir, "pending"),
		symlink:      filepath.Join(dir, "umbgs"),
		httpClient:   &http.Client{Timeout: 5 * time.Second},
		healthyDelay: 50 * time.Millisecond,
	}
	// Seed the active slot file so inactiveSlot() starts predictably at "b".
	if err := os.WriteFile(u.activePath, []byte("a"), 0644); err != nil {
		t.Fatalf("seed active file: %v", err)
	}
	return u, dir
}

// fakeBinary is the "binary" content the mock GitHub serves. Small enough
// to eyeball, non-empty so the hash matters.
var fakeBinary = []byte("this is a fake umbgs binary for testing purposes\n")

func fakeBinaryHash() string {
	sum := sha256.Sum256(fakeBinary)
	return hex.EncodeToString(sum[:])
}

// mockGitHub builds an httptest.Server that responds like the subset of
// the GitHub REST API our updater uses. Individual tests override handlers
// via the returned *mockState to simulate different scenarios.
//
// By default the mock publishes ONLY the raw binary + sha256 so existing
// tests stay deterministic. Set publishGZ=true to also expose the .gz
// asset — that flips the updater into its gzipped code path.
type mockState struct {
	latestStatus  int
	latestBody    string
	shaStatus     int
	shaBody       string
	binaryStatus  int
	binaryBody    []byte
	gzStatus      int
	gzBody        []byte // raw gzip bytes served at /download/gz
	publishGZ     bool   // include the .gz asset in the release JSON
	omitRawBinary bool   // omit the raw binary asset (force gz-only)
	requestsSeen  []string
	assetsMissing bool // omit all download assets from the release response
}

// gzipBytes is a tiny helper for tests that need a valid gzip stream of
// their binary body.
func gzipBytes(t *testing.T, data []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	if _, err := gw.Write(data); err != nil {
		t.Fatalf("gzip write: %v", err)
	}
	if err := gw.Close(); err != nil {
		t.Fatalf("gzip close: %v", err)
	}
	return buf.Bytes()
}

func newMockGitHub(t *testing.T) (*httptest.Server, *mockState) {
	t.Helper()
	state := &mockState{
		latestStatus: http.StatusOK,
		shaStatus:    http.StatusOK,
		binaryStatus: http.StatusOK,
		gzStatus:     http.StatusOK,
		binaryBody:   fakeBinary,
		shaBody:      fakeBinaryHash() + "  umbgs-linux-arm64\n",
	}

	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)

	// Fill in the asset URLs to point back at this same server once it's
	// started (we need srv.URL which isn't known until after NewServer).
	makeLatestBody := func() string {
		if state.latestBody != "" {
			return state.latestBody
		}
		if state.assetsMissing {
			return `{"tag_name":"v0.0.2","draft":false,"prerelease":false,"assets":[]}`
		}
		var assets []string
		if !state.omitRawBinary {
			assets = append(assets,
				fmt.Sprintf(`{"name":"umbgs-linux-arm64","browser_download_url":"%s/download/bin","size":%d}`,
					srv.URL, len(state.binaryBody)))
		}
		if state.publishGZ {
			// Lazily build a gzip body from binaryBody if the test didn't
			// provide its own. Size is reported as the compressed length,
			// which is what the real GitHub API would return for a .gz.
			if state.gzBody == nil {
				state.gzBody = gzipBytes(t, state.binaryBody)
			}
			assets = append(assets,
				fmt.Sprintf(`{"name":"umbgs-linux-arm64.gz","browser_download_url":"%s/download/gz","size":%d}`,
					srv.URL, len(state.gzBody)))
		}
		assets = append(assets,
			fmt.Sprintf(`{"name":"umbgs-linux-arm64.sha256","browser_download_url":"%s/download/sha","size":%d}`,
				srv.URL, len(state.shaBody)))
		return fmt.Sprintf(`{
			"tag_name": "v0.0.2",
			"draft": false,
			"prerelease": false,
			"assets": [%s]
		}`, strings.Join(assets, ","))
	}

	mux.HandleFunc("/repos/test-owner/test-repo/releases/latest", func(w http.ResponseWriter, r *http.Request) {
		state.requestsSeen = append(state.requestsSeen, r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(state.latestStatus)
		_, _ = w.Write([]byte(makeLatestBody()))
	})
	mux.HandleFunc("/repos/test-owner/test-repo/releases", func(w http.ResponseWriter, r *http.Request) {
		// "beta" channel hits this list endpoint.
		state.requestsSeen = append(state.requestsSeen, r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(state.latestStatus)
		// Wrap the single release in a JSON array.
		_, _ = w.Write([]byte("[" + makeLatestBody() + "]"))
	})
	mux.HandleFunc("/download/bin", func(w http.ResponseWriter, r *http.Request) {
		state.requestsSeen = append(state.requestsSeen, r.URL.Path)
		w.WriteHeader(state.binaryStatus)
		_, _ = w.Write(state.binaryBody)
	})
	mux.HandleFunc("/download/gz", func(w http.ResponseWriter, r *http.Request) {
		state.requestsSeen = append(state.requestsSeen, r.URL.Path)
		w.WriteHeader(state.gzStatus)
		_, _ = w.Write(state.gzBody)
	})
	mux.HandleFunc("/download/sha", func(w http.ResponseWriter, r *http.Request) {
		state.requestsSeen = append(state.requestsSeen, r.URL.Path)
		w.WriteHeader(state.shaStatus)
		_, _ = w.Write([]byte(state.shaBody))
	})

	return srv, state
}

// TestCheck_UpdateAvailable verifies that Check reports an available
// update when the release tag differs from the current version AND the
// required assets are present. Check must NOT download anything — that's
// Apply's job. This is the test that guards against Check regressing into
// the "download everything synchronously" behavior that was breaking the
// dashboard UX on slow uplinks.
func TestCheck_UpdateAvailable(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()

	u, dir := newTestUpdater(t, srv.URL)

	result, err := u.Check(context.Background())
	if err != nil {
		t.Fatalf("Check: %v", err)
	}

	if !result.Available {
		t.Errorf("expected Available=true, got false (message: %q)", result.Message)
	}
	if result.LatestVersion != "v0.0.2" {
		t.Errorf("LatestVersion = %q, want v0.0.2", result.LatestVersion)
	}
	if result.CurrentVersion != "v0.0.1" {
		t.Errorf("CurrentVersion = %q, want v0.0.1", result.CurrentVersion)
	}
	if !strings.Contains(result.Message, "v0.0.2") {
		t.Errorf("Message = %q, expected it to mention v0.0.2", result.Message)
	}

	// Check must NOT have hit any /download/ endpoints.
	for _, p := range state.requestsSeen {
		if strings.HasPrefix(p, "/download/") {
			t.Errorf("Check should not download, but hit %q", p)
		}
	}
	// Slot B must still be absent since Check doesn't touch the filesystem.
	if _, err := os.Stat(filepath.Join(dir, "umbgs-b")); !os.IsNotExist(err) {
		t.Errorf("slot b should not exist after Check, got err=%v", err)
	}
}

// TestApply_HappyPath exercises the whole install flow: Apply fetches
// release metadata, downloads sha256, downloads binary, verifies hash,
// swaps slots, and transitions State to PhaseApplied.
func TestApply_HappyPath(t *testing.T) {
	srv, _ := newMockGitHub(t)
	defer srv.Close()

	u, dir := newTestUpdater(t, srv.URL)

	if err := u.Apply(context.Background()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	st := u.State()
	if st.Phase != PhaseApplied {
		t.Errorf("Phase = %q, want %q (msg: %q)", st.Phase, PhaseApplied, st.Message)
	}
	if st.LatestVersion != "v0.0.2" {
		t.Errorf("LatestVersion = %q, want v0.0.2", st.LatestVersion)
	}
	if !strings.Contains(st.Message, "v0.0.2") {
		t.Errorf("Message = %q, expected it to mention v0.0.2", st.Message)
	}

	// slot B should now contain the downloaded binary.
	got, err := os.ReadFile(filepath.Join(dir, "umbgs-b"))
	if err != nil {
		t.Fatalf("read slot b: %v", err)
	}
	if string(got) != string(fakeBinary) {
		t.Errorf("slot b content mismatch: got %q, want %q", got, fakeBinary)
	}

	// active file should now say "b".
	active, err := os.ReadFile(filepath.Join(dir, "active"))
	if err != nil {
		t.Fatalf("read active: %v", err)
	}
	if string(active) != "b" {
		t.Errorf("active = %q, want b", active)
	}

	// pending file should exist and say "b" (watchdog will clear it after healthy boot).
	pending, err := os.ReadFile(filepath.Join(dir, "pending"))
	if err != nil {
		t.Fatalf("read pending: %v", err)
	}
	if string(pending) != "b" {
		t.Errorf("pending = %q, want b", pending)
	}
}

// TestCheck_AlreadyUpToDate verifies the "current tag matches release" path
// returns a friendly message and Available=false.
func TestCheck_AlreadyUpToDate(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()

	u, dir := newTestUpdater(t, srv.URL)
	u.version = "v0.0.2" // match the mocked release tag

	result, err := u.Check(context.Background())
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if result.Available {
		t.Error("expected Available=false when already on latest")
	}
	if !strings.Contains(result.Message, "up to date") {
		t.Errorf("Message = %q, expected 'up to date'", result.Message)
	}
	// No download requests should have been made — only metadata.
	for _, p := range state.requestsSeen {
		if strings.HasPrefix(p, "/download/") {
			t.Errorf("unexpected download request %q when already up to date", p)
		}
	}
	// slot B must still be absent (or empty) since we didn't write to it.
	if _, err := os.Stat(filepath.Join(dir, "umbgs-b")); !os.IsNotExist(err) {
		t.Errorf("slot b should not exist when no update applied, got err=%v", err)
	}
}

// TestCheck_NoReleases verifies that a 404 on /releases/latest is treated
// as a successful "nothing to do" rather than an error. Matters for a
// freshly-tagged project where the release workflow hasn't published yet.
func TestCheck_NoReleases(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	state.latestStatus = http.StatusNotFound
	state.latestBody = `{"message":"Not Found"}`

	u, _ := newTestUpdater(t, srv.URL)
	result, err := u.Check(context.Background())
	if err != nil {
		t.Fatalf("Check returned error for 404: %v", err)
	}
	if result.Available {
		t.Error("expected Available=false for no releases")
	}
	if !strings.Contains(result.Message, "No releases") {
		t.Errorf("Message = %q, expected 'No releases'", result.Message)
	}
}

// TestCheck_MissingAsset verifies Check surfaces an error when the release
// metadata is valid but the required asset is missing. This catches the
// failure at Check time rather than at Apply time so the dashboard never
// offers an Install button for a broken release.
func TestCheck_MissingAsset(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	state.assetsMissing = true

	u, _ := newTestUpdater(t, srv.URL)
	_, err := u.Check(context.Background())
	if err == nil {
		t.Fatal("expected error when release has no assets, got nil")
	}
	if !strings.Contains(err.Error(), "missing required assets") {
		t.Errorf("error = %v, expected 'missing required assets'", err)
	}
}

// TestApply_HashMismatch verifies the downloaded binary is discarded if it
// doesn't match the published sha256. This is the tampering/corruption
// guard. State must transition to PhaseError.
func TestApply_HashMismatch(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	// Serve a sha256 that doesn't match the binary body.
	state.shaBody = strings.Repeat("a", 64) + "  umbgs-linux-arm64\n"

	u, dir := newTestUpdater(t, srv.URL)
	err := u.Apply(context.Background())
	if err == nil {
		t.Fatal("expected error on hash mismatch, got nil")
	}
	if !strings.Contains(err.Error(), "hash mismatch") {
		t.Errorf("error = %v, expected 'hash mismatch'", err)
	}
	if st := u.State(); st.Phase != PhaseError {
		t.Errorf("Phase = %q, want %q (msg: %q)", st.Phase, PhaseError, st.Message)
	}
	// Slot B must not exist (cleanup should have removed the tmp file).
	if _, statErr := os.Stat(filepath.Join(dir, "umbgs-b")); !os.IsNotExist(statErr) {
		t.Errorf("slot b should not exist after failed update, got err=%v", statErr)
	}
	if _, statErr := os.Stat(filepath.Join(dir, "umbgs-b.tmp")); !os.IsNotExist(statErr) {
		t.Errorf("tmp file should not exist after failed update, got err=%v", statErr)
	}
	// Active file must still say "a" — slot swap must not have happened.
	active, _ := os.ReadFile(filepath.Join(dir, "active"))
	if string(active) != "a" {
		t.Errorf("active = %q, want a (slot should not swap on failure)", active)
	}
}

// TestApply_Concurrent verifies that a second Apply while the first is
// still running returns ErrApplyInProgress instead of racing through the
// slot swap. This matters because the dashboard handler spawns a goroutine
// per request — a double-click must not trigger two downloads.
func TestApply_Concurrent(t *testing.T) {
	srv, _ := newMockGitHub(t)
	defer srv.Close()
	u, _ := newTestUpdater(t, srv.URL)

	// Manually grab the apply lock to simulate an in-progress Apply. We
	// can't rely on a slow HTTP server because apply() runs fast on
	// localhost. This exercises the same TryLock guard that production
	// goroutines hit.
	u.applyMu.Lock()
	defer u.applyMu.Unlock()

	err := u.Apply(context.Background())
	if !errors.Is(err, ErrApplyInProgress) {
		t.Errorf("expected ErrApplyInProgress, got %v", err)
	}
}

// TestApply_GzippedHappyPath verifies the end-to-end install when the
// release publishes a .gz asset. The updater must stream the compressed
// download through gzip.NewReader, hash the DECOMPRESSED bytes (to match
// the published sha256 which describes the raw binary), and land the
// uncompressed bytes in the slot file. This is the headline test for the
// bandwidth optimization — if it breaks, chase-vehicle updates fall back
// to a 3x larger download silently.
func TestApply_GzippedHappyPath(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	state.publishGZ = true
	state.omitRawBinary = true // force gz-only, matching a .gz-first release

	u, dir := newTestUpdater(t, srv.URL)

	if err := u.Apply(context.Background()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	// Assert the updater hit /download/gz and NOT /download/bin.
	var hitGZ, hitBin bool
	for _, p := range state.requestsSeen {
		if p == "/download/gz" {
			hitGZ = true
		}
		if p == "/download/bin" {
			hitBin = true
		}
	}
	if !hitGZ {
		t.Errorf("expected /download/gz hit, requests: %v", state.requestsSeen)
	}
	if hitBin {
		t.Errorf("did not expect /download/bin when .gz is published, requests: %v", state.requestsSeen)
	}

	// Slot B must contain the DECOMPRESSED binary, not the .gz blob.
	got, err := os.ReadFile(filepath.Join(dir, "umbgs-b"))
	if err != nil {
		t.Fatalf("read slot b: %v", err)
	}
	if !bytes.Equal(got, fakeBinary) {
		t.Errorf("slot b content mismatch: got %d bytes, want %d (decompressed)",
			len(got), len(fakeBinary))
	}

	st := u.State()
	if st.Phase != PhaseApplied {
		t.Errorf("Phase = %q, want applied (msg: %q)", st.Phase, st.Message)
	}
}

// TestApply_GzippedFallbackToRaw verifies the updater falls back to the
// raw binary when a release publishes only the raw asset — e.g., a
// hand-cut release or something predating the .gz publication. This
// matters because dropping support for the raw asset would brick updates
// on any old release.
func TestApply_GzippedFallbackToRaw(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	// Default state: publishGZ=false, raw binary published. Matches the
	// legacy path.

	u, dir := newTestUpdater(t, srv.URL)
	if err := u.Apply(context.Background()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	var hitGZ, hitBin bool
	for _, p := range state.requestsSeen {
		if p == "/download/gz" {
			hitGZ = true
		}
		if p == "/download/bin" {
			hitBin = true
		}
	}
	if hitGZ {
		t.Errorf("should not hit .gz when raw is the only asset, requests: %v", state.requestsSeen)
	}
	if !hitBin {
		t.Errorf("expected /download/bin fallback, requests: %v", state.requestsSeen)
	}

	got, err := os.ReadFile(filepath.Join(dir, "umbgs-b"))
	if err != nil {
		t.Fatalf("read slot b: %v", err)
	}
	if !bytes.Equal(got, fakeBinary) {
		t.Errorf("slot b content mismatch: got %q, want %q", got, fakeBinary)
	}
}

// TestApply_GzippedCorrupt verifies a corrupt .gz body surfaces as an
// error and the slot is NOT swapped. The corruption is injected by
// serving the raw (uncompressed) binary as if it were a .gz — gzip.Reader
// will fail on the magic number check.
func TestApply_GzippedCorrupt(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	state.publishGZ = true
	state.omitRawBinary = true
	state.gzBody = []byte("not actually a gzip stream, just raw bytes")

	u, dir := newTestUpdater(t, srv.URL)
	err := u.Apply(context.Background())
	if err == nil {
		t.Fatal("expected error for corrupt gzip, got nil")
	}
	if !strings.Contains(err.Error(), "gzip") {
		t.Errorf("error = %v, expected mention of gzip", err)
	}
	if st := u.State(); st.Phase != PhaseError {
		t.Errorf("Phase = %q, want error", st.Phase)
	}
	// Slot B must not exist (no swap on failure).
	if _, statErr := os.Stat(filepath.Join(dir, "umbgs-b")); !os.IsNotExist(statErr) {
		t.Errorf("slot b should not exist after corrupt download, got err=%v", statErr)
	}
	// Active file must still say "a".
	active, _ := os.ReadFile(filepath.Join(dir, "active"))
	if string(active) != "a" {
		t.Errorf("active = %q, want a", active)
	}
}

// TestApply_ProgressTracked verifies that Apply publishes download
// progress via State(). We use a chunked body so multiple Read() calls
// happen during io.Copy, and check that State.Downloaded advances.
func TestApply_ProgressTracked(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	// Bigger body so there's actually something to report. Small enough
	// to stay fast in tests.
	state.binaryBody = []byte(strings.Repeat("x", 64*1024))
	sum := sha256.Sum256(state.binaryBody)
	state.shaBody = hex.EncodeToString(sum[:]) + "  umbgs-linux-arm64\n"

	u, _ := newTestUpdater(t, srv.URL)
	if err := u.Apply(context.Background()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	st := u.State()
	if st.Phase != PhaseApplied {
		t.Errorf("Phase = %q, want applied", st.Phase)
	}
	// After a successful Apply, Downloaded should reflect the full body.
	// The progressReader always flushes on EOF so this should be exact.
	if st.Downloaded != int64(len(state.binaryBody)) {
		t.Errorf("Downloaded = %d, want %d", st.Downloaded, len(state.binaryBody))
	}
}

// TestCheck_ServerError verifies that a non-404, non-200 response from
// GitHub surfaces as an error rather than silently succeeding. 500s and
// 403 rate limits both go through this path.
func TestCheck_ServerError(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	state.latestStatus = http.StatusForbidden
	state.latestBody = `{"message":"API rate limit exceeded"}`

	u, _ := newTestUpdater(t, srv.URL)
	_, err := u.Check(context.Background())
	if err == nil {
		t.Fatal("expected error on 403, got nil")
	}
	if !strings.Contains(err.Error(), "HTTP 403") {
		t.Errorf("error = %v, expected 'HTTP 403'", err)
	}
}

// TestCheck_UpdatesDisabled is currently not enforceable from Check — the
// disabled flag only short-circuits Run(). Kept as a documentation test:
// callers must gate on cfg.Update.Enabled themselves.
// (No test body; this comment is the test.)

// setChannel swaps the updater's config.Manager for a fresh one with the
// given channel. Using Manager.Update would call config.Save which writes
// to /data, unavailable in tests.
func setChannel(u *Updater, channel string) {
	c := config.Defaults()
	c.Update.Enabled = true
	c.Update.Channel = channel
	u.cfg = config.NewManager(&c, "")
}

// TestCheck_BetaChannel verifies beta channel hits /releases (list) rather
// than /releases/latest.
func TestCheck_BetaChannel(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()

	u, _ := newTestUpdater(t, srv.URL)
	setChannel(u, "beta")

	_, err := u.Check(context.Background())
	if err != nil {
		t.Fatalf("Check: %v", err)
	}

	// Verify the list endpoint was hit, not the "latest" endpoint.
	var hitList, hitLatest bool
	for _, p := range state.requestsSeen {
		if p == "/repos/test-owner/test-repo/releases" {
			hitList = true
		}
		if p == "/repos/test-owner/test-repo/releases/latest" {
			hitLatest = true
		}
	}
	if !hitList {
		t.Errorf("beta channel should hit /releases, requests: %v", state.requestsSeen)
	}
	if hitLatest {
		t.Errorf("beta channel should NOT hit /releases/latest, requests: %v", state.requestsSeen)
	}
}

// TestCheck_UnknownChannel verifies a config typo produces a clear error
// rather than silently falling through to stable.
func TestCheck_UnknownChannel(t *testing.T) {
	srv, _ := newMockGitHub(t)
	defer srv.Close()

	u, _ := newTestUpdater(t, srv.URL)
	setChannel(u, "nightly") // not supported

	_, err := u.Check(context.Background())
	if err == nil {
		t.Fatal("expected error for unknown channel")
	}
	if !strings.Contains(err.Error(), "unknown update channel") {
		t.Errorf("error = %v, expected 'unknown update channel'", err)
	}
}

// TestRun_ClearsPendingAfterHealthyDelay verifies that Run() waits for
// healthyDelay and then clears the pending marker. This is the "happy
// boot" path: new binary starts, doesn't crash, watchdog sees the marker
// disappear and takes no action.
func TestRun_ClearsPendingAfterHealthyDelay(t *testing.T) {
	u, dir := newTestUpdater(t, "http://unused")
	// Seed a pending marker as if a prior update had just been applied.
	pendingPath := filepath.Join(dir, "pending")
	if err := os.WriteFile(pendingPath, []byte("b"), 0644); err != nil {
		t.Fatalf("seed pending: %v", err)
	}

	// Run in a goroutine; cancel once we've observed the clear.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- u.Run(ctx) }()

	// Poll for the marker to disappear. healthyDelay is 50ms in tests,
	// so this should complete well under the deadline.
	deadline := time.Now().Add(2 * time.Second)
	for {
		if _, err := os.Stat(pendingPath); os.IsNotExist(err) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("pending marker was not cleared within 2s")
		}
		time.Sleep(10 * time.Millisecond)
	}

	cancel()
	if err := <-done; err != nil && err != context.Canceled {
		t.Errorf("Run returned unexpected error: %v", err)
	}
}

// TestRun_PreservesPendingOnEarlyShutdown verifies that if the process is
// torn down before healthyDelay elapses, the pending marker stays in place.
// This is the crash-loop-defense regression test: a new binary that can't
// even make it to healthyDelay of runtime must leave the marker for the
// watchdog to observe on the NEXT run.
func TestRun_PreservesPendingOnEarlyShutdown(t *testing.T) {
	u, dir := newTestUpdater(t, "http://unused")
	// Use a longer delay here so we can cancel before it fires.
	u.healthyDelay = 10 * time.Second

	pendingPath := filepath.Join(dir, "pending")
	if err := os.WriteFile(pendingPath, []byte("b"), 0644); err != nil {
		t.Fatalf("seed pending: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- u.Run(ctx) }()

	// Simulate a crash/shutdown well before healthyDelay.
	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case err := <-done:
		if err != context.Canceled {
			t.Errorf("Run returned %v, expected context.Canceled", err)
		}
	case <-time.After(time.Second):
		t.Fatal("Run did not return after cancel")
	}

	// The marker MUST still exist. This is the whole point of the delay.
	if _, err := os.Stat(pendingPath); err != nil {
		t.Fatalf("pending marker was cleared despite early shutdown: %v", err)
	}
}

// TestRun_DisabledSkipsDelay verifies that when updates are disabled, Run
// returns immediately on ctx cancel without waiting for healthyDelay and
// without touching the pending marker.
func TestRun_DisabledSkipsDelay(t *testing.T) {
	u, dir := newTestUpdater(t, "http://unused")
	u.healthyDelay = 10 * time.Second // would hang the test if reached

	// Disable updates.
	c := config.Defaults()
	c.Update.Enabled = false
	u.cfg = config.NewManager(&c, "")

	// Seed pending; assert it's untouched.
	pendingPath := filepath.Join(dir, "pending")
	if err := os.WriteFile(pendingPath, []byte("b"), 0644); err != nil {
		t.Fatalf("seed pending: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- u.Run(ctx) }()

	time.Sleep(20 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Run did not return after cancel (delay should have been skipped)")
	}

	if _, err := os.Stat(pendingPath); err != nil {
		t.Errorf("pending marker was touched when updates disabled: %v", err)
	}
}

// TestFetchSHA256_Parsing covers the tiny format parser for sha256sum files.
// Direct unit test so we don't need httptest for edge cases.
func TestFetchSHA256_Parsing(t *testing.T) {
	cases := []struct {
		name    string
		body    string
		want    string
		wantErr bool
	}{
		{
			name: "standard format",
			body: "abc123" + strings.Repeat("0", 58) + "  umbgs-linux-arm64\n",
			want: "abc123" + strings.Repeat("0", 58),
		},
		{
			name: "no filename (plain hash)",
			body: strings.Repeat("f", 64) + "\n",
			want: strings.Repeat("f", 64),
		},
		{
			name: "uppercase normalized to lowercase",
			body: strings.Repeat("F", 64) + "  x\n",
			want: strings.Repeat("f", 64),
		},
		{
			name:    "empty",
			body:    "",
			wantErr: true,
		},
		{
			name:    "too short",
			body:    "abc123  x",
			wantErr: true,
		},
		{
			name:    "too long",
			body:    strings.Repeat("a", 65) + "  x",
			wantErr: true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			u := &Updater{
				version:    "test",
				httpClient: &http.Client{Timeout: time.Second},
			}
			got, err := u.fetchSHA256(context.Background(), srv.URL)
			if tc.wantErr {
				if err == nil {
					t.Errorf("expected error, got hash %q", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

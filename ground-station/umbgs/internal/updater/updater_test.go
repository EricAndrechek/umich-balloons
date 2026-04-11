package updater

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
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
type mockState struct {
	latestStatus  int
	latestBody    string
	shaStatus     int
	shaBody       string
	binaryStatus  int
	binaryBody    []byte
	requestsSeen  []string
	assetsMissing bool // omit assets from the release response
}

func newMockGitHub(t *testing.T) (*httptest.Server, *mockState) {
	t.Helper()
	state := &mockState{
		latestStatus: http.StatusOK,
		shaStatus:    http.StatusOK,
		binaryStatus: http.StatusOK,
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
		return fmt.Sprintf(`{
			"tag_name": "v0.0.2",
			"draft": false,
			"prerelease": false,
			"assets": [
				{"name":"umbgs-linux-arm64","browser_download_url":"%s/download/bin","size":%d},
				{"name":"umbgs-linux-arm64.sha256","browser_download_url":"%s/download/sha","size":%d}
			]
		}`, srv.URL, len(state.binaryBody), srv.URL, len(state.shaBody))
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
	mux.HandleFunc("/download/sha", func(w http.ResponseWriter, r *http.Request) {
		state.requestsSeen = append(state.requestsSeen, r.URL.Path)
		w.WriteHeader(state.shaStatus)
		_, _ = w.Write([]byte(state.shaBody))
	})

	return srv, state
}

// TestCheck_HappyPath exercises the whole flow: fetch release metadata,
// download sha256, download binary, verify hash, swap slots. This is the
// test that guards against the "updater is a no-op" regression that
// shipped with the CF Worker placeholder.
func TestCheck_HappyPath(t *testing.T) {
	srv, _ := newMockGitHub(t)
	defer srv.Close()

	u, dir := newTestUpdater(t, srv.URL)

	result, err := u.Check(context.Background())
	if err != nil {
		t.Fatalf("Check: %v", err)
	}

	if !result.Updated {
		t.Errorf("expected Updated=true, got false (message: %q)", result.Message)
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
// returns a friendly message and does not download anything.
func TestCheck_AlreadyUpToDate(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()

	u, dir := newTestUpdater(t, srv.URL)
	u.version = "v0.0.2" // match the mocked release tag

	result, err := u.Check(context.Background())
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if result.Updated {
		t.Error("expected Updated=false when already on latest")
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
	if result.Updated {
		t.Error("expected Updated=false for no releases")
	}
	if !strings.Contains(result.Message, "no releases") {
		t.Errorf("Message = %q, expected 'no releases'", result.Message)
	}
}

// TestCheck_MissingAsset verifies we surface an error (rather than silently
// succeeding) when the release metadata is valid but the required asset is
// missing. This would happen if the CI workflow was edited to rename the
// asset without also updating the updater constants.
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

// TestCheck_HashMismatch verifies the downloaded binary is discarded if it
// doesn't match the published sha256. This is the tampering/corruption guard.
func TestCheck_HashMismatch(t *testing.T) {
	srv, state := newMockGitHub(t)
	defer srv.Close()
	// Serve a sha256 that doesn't match the binary body.
	state.shaBody = strings.Repeat("a", 64) + "  umbgs-linux-arm64\n"

	u, dir := newTestUpdater(t, srv.URL)
	_, err := u.Check(context.Background())
	if err == nil {
		t.Fatal("expected error on hash mismatch, got nil")
	}
	if !strings.Contains(err.Error(), "hash mismatch") {
		t.Errorf("error = %v, expected 'hash mismatch'", err)
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

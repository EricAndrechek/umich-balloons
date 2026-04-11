package uploader

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/buffer"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// fakeOnline is a test double for OnlineChecker that's always online.
type fakeOnline struct {
	online atomic.Bool
}

func (f *fakeOnline) Online() bool { return f.online.Load() }

// newTestUploader wires up a minimal uploader with its own SQLite buffer
// in a temp dir, a config pointed at apiURL, and a fake "online" checker.
func newTestUploader(t *testing.T, apiURL string) (*Uploader, *buffer.Store, chan types.PacketEvent) {
	t.Helper()

	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	dbPath := filepath.Join(t.TempDir(), "buffer.db")
	buf, err := buffer.OpenAt(dbPath, logger)
	if err != nil {
		t.Fatalf("open buffer: %v", err)
	}
	t.Cleanup(func() { buf.Close() })

	cfg := config.Defaults()
	cfg.APIUrl = apiURL
	cfg.Callsign = "KD8CJT"
	cfg.SSID = 9
	cfgMgr := config.NewManager(&cfg, "")

	conn := &fakeOnline{}
	conn.online.Store(true)

	pktChan := make(chan types.Packet, 16)
	evtChan := make(chan types.PacketEvent, 16)

	return New(cfgMgr, buf, conn, pktChan, evtChan, logger), buf, evtChan
}

// TestPermanentError_HandlePacket verifies that a 4xx response from the
// server causes the packet to be routed to the failed table instead of
// being buffered for retry. This is the core fix for the "LoRa pre-fix
// packets buffer forever" bug.
func TestPermanentError_HandlePacket(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":"position 0,0 rejected (likely invalid GPS)"}`))
	}))
	defer srv.Close()

	u, buf, evtChan := newTestUploader(t, srv.URL)

	pkt := types.Packet{
		Source:   "lora",
		RawData:  `{"call":"TESTPL","lat":0,"lon":0}`,
		Endpoint: "/lora",
		Sender:   "KD8CJT-9",
		Time:     time.Now().UTC(),
	}

	u.handlePacket(context.Background(), pkt)

	// The request should have been made exactly once — permanent errors
	// must not be retried by the upload() retry loop.
	if got := hits.Load(); got != 1 {
		t.Errorf("expected 1 request, got %d (permanent errors should not retry)", got)
	}

	// Packet should be in the failed table, not buffered.
	if depth := buf.Depth(); depth != 0 {
		t.Errorf("expected buffer depth 0, got %d (rejected packet should not be buffered)", depth)
	}
	if failed := buf.FailedCount(); failed != 1 {
		t.Errorf("expected failed count 1, got %d", failed)
	}

	// Dashboard event should be StatusFailed.
	select {
	case evt := <-evtChan:
		if evt.Status != types.StatusFailed {
			t.Errorf("expected StatusFailed event, got %v", evt.Status)
		}
		if evt.Error == "" {
			t.Error("expected non-empty error in event")
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for packet event")
	}
}

// TestTransientError_HandlePacket verifies that a 5xx response causes
// the packet to be buffered for retry (the existing behavior).
func TestTransientError_HandlePacket(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(`upstream error`))
	}))
	defer srv.Close()

	u, buf, evtChan := newTestUploader(t, srv.URL)

	pkt := types.Packet{
		Source:   "aprs",
		RawData:  "KD8CJT>APRS:test",
		Endpoint: "/aprs",
		Sender:   "KD8CJT-9",
		Time:     time.Now().UTC(),
	}

	u.handlePacket(context.Background(), pkt)

	if depth := buf.Depth(); depth != 1 {
		t.Errorf("expected buffer depth 1 (transient errors retry via buffer), got %d", depth)
	}
	if failed := buf.FailedCount(); failed != 0 {
		t.Errorf("expected failed count 0, got %d (5xx should not go to failed table)", failed)
	}

	select {
	case evt := <-evtChan:
		if evt.Status != types.StatusBuffered {
			t.Errorf("expected StatusBuffered event, got %v", evt.Status)
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for packet event")
	}
}

// TestFlushBuffer_PermanentError verifies that a permanent failure during
// buffer flush removes the packet from the buffered table, records it as
// failed, and continues draining instead of stopping. This is the
// "recovery" path for pre-fix packets buffered while offline and then
// rejected once the server is reachable.
func TestFlushBuffer_PermanentError(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":"missing required field: latitude"}`))
	}))
	defer srv.Close()

	u, buf, _ := newTestUploader(t, srv.URL)

	// Seed the buffer with two pre-rejected packets.
	for i := 0; i < 2; i++ {
		if err := buf.Enqueue(types.Packet{
			Source:   "lora",
			RawData:  `{"call":"TESTPL"}`,
			Endpoint: "/lora",
			Sender:   "KD8CJT-9",
			Time:     time.Now().UTC(),
		}); err != nil {
			t.Fatalf("enqueue: %v", err)
		}
	}
	if depth := buf.Depth(); depth != 2 {
		t.Fatalf("setup: expected buffer depth 2, got %d", depth)
	}

	u.flushBuffer(context.Background())

	// Both packets should have been attempted — no early exit on permanent
	// failures during flush.
	if got := hits.Load(); got != 2 {
		t.Errorf("expected 2 requests during flush, got %d", got)
	}
	if depth := buf.Depth(); depth != 0 {
		t.Errorf("expected buffer depth 0 after flush, got %d", depth)
	}
	if failed := buf.FailedCount(); failed != 2 {
		t.Errorf("expected failed count 2, got %d", failed)
	}
}

// TestFlushBuffer_TransientError verifies that a 5xx during flush stops
// draining so packets get retried on the next flush.
func TestFlushBuffer_TransientError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	u, buf, _ := newTestUploader(t, srv.URL)

	for i := 0; i < 3; i++ {
		if err := buf.Enqueue(types.Packet{
			Source: "aprs", Endpoint: "/aprs", Sender: "KD8CJT-9",
			Time: time.Now().UTC(), RawData: "test",
		}); err != nil {
			t.Fatalf("enqueue: %v", err)
		}
	}

	u.flushBuffer(context.Background())

	if depth := buf.Depth(); depth != 3 {
		t.Errorf("expected buffer depth 3 (5xx should not drain), got %d", depth)
	}
	if failed := buf.FailedCount(); failed != 0 {
		t.Errorf("expected failed count 0, got %d", failed)
	}
}

// TestIsPermanent_ErrorTypes checks the sentinel type-check helper.
func TestIsPermanent_ErrorTypes(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want bool
	}{
		{"nil", nil, false},
		{"permanent 400", &permanentError{status: 400, body: "bad"}, true},
		{"permanent 404", &permanentError{status: 404}, true},
		{"plain error", errPlain("HTTP 503"), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isPermanent(tc.err); got != tc.want {
				t.Errorf("isPermanent(%v) = %v, want %v", tc.err, got, tc.want)
			}
		})
	}
}

// errPlain is a trivial error type for tests.
type errPlain string

func (e errPlain) Error() string { return string(e) }

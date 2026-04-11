package dashboard

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/buffer"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/system"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/updater"
)

var wsUpgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// Server is the dashboard HTTP server.
type Server struct {
	cfg     *config.Manager
	hub     *Hub
	stats   *system.Stats
	buf     *buffer.Store
	updater *updater.Updater
	logger  *slog.Logger
	mux     *http.ServeMux
}

// NewServer creates the dashboard server.
func NewServer(
	cfg *config.Manager,
	hub *Hub,
	stats *system.Stats,
	buf *buffer.Store,
	upd *updater.Updater,
	logger *slog.Logger,
) *Server {
	s := &Server{
		cfg:     cfg,
		hub:     hub,
		stats:   stats,
		buf:     buf,
		updater: upd,
		logger:  logger.With("service", "dashboard"),
		mux:     http.NewServeMux(),
	}
	s.routes()
	return s
}

func (s *Server) routes() {
	s.mux.HandleFunc("/ws", s.handleWS)
	s.mux.HandleFunc("/api/config", s.handleConfig)
	s.mux.HandleFunc("/api/stats", s.handleStats)
	s.mux.HandleFunc("/api/failed", s.handleFailed)
	s.mux.HandleFunc("/api/update/check", s.handleUpdateCheck)
	s.mux.HandleFunc("/api/update/apply", s.handleUpdateApply)
	s.mux.HandleFunc("/api/update/status", s.handleUpdateStatus)
	s.mux.HandleFunc("/api/reboot", s.handleReboot)
	s.mux.HandleFunc("/api/crashlogs", s.handleCrashLogs)
	s.mux.HandleFunc("/api/services/restart/", s.handleServiceRestart)
	s.mux.HandleFunc("/sw.js", s.handleServiceWorker)
	// Serve embedded web files with no-cache headers so updates are picked up immediately.
	fs := http.FileServer(http.FS(webFS))
	s.mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
		fs.ServeHTTP(w, r)
	})
}

// Run starts the HTTP server. It also binds a secondary listener on port
// 80 that serves the captive portal probes and redirects everything else
// to the real dashboard port. That lets phones that join the umbgs-ap
// hotspot get a "Sign in to network" notification and land on the UI
// without the operator having to type a URL or port number.
func (s *Server) Run(ctx context.Context) error {
	c := s.cfg.Get()
	addr := fmt.Sprintf(":%d", c.Dashboard.Port)
	srv := &http.Server{
		Addr:    addr,
		Handler: s.mux,
	}

	portalAddr := ":80"
	portalMux := http.NewServeMux()
	s.captivePortalRoutes(portalMux, c.Dashboard.Port)
	portalSrv := &http.Server{
		Addr:    portalAddr,
		Handler: portalMux,
	}

	// Bind the :80 listener synchronously so a bind failure surfaces in
	// the logs immediately as a distinct "failed to bind" line, rather
	// than hiding inside a background goroutine. This was diagnosing a
	// field issue where phones joining the AP hotspot saw "No Internet
	// Connection" but no captive portal sheet — which would be the
	// expected symptom if the :80 listener never actually bound.
	portalLn, portalBindErr := net.Listen("tcp", portalAddr)
	if portalBindErr != nil {
		s.logger.Warn("captive portal bind failed — phones joining the AP hotspot will not see a captive portal sheet",
			"addr", portalAddr, "error", portalBindErr)
	}

	go func() {
		<-ctx.Done()
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		srv.Shutdown(shutCtx)
		portalSrv.Shutdown(shutCtx)
	}()

	// Serve the captive portal in a goroutine only if the bind succeeded.
	// Best-effort: we continue even if :80 couldn't be bound — the main
	// dashboard must still come up on its configured port.
	if portalLn != nil {
		go func() {
			s.logger.Info("captive portal listening", "addr", portalAddr)
			if err := portalSrv.Serve(portalLn); err != nil && err != http.ErrServerClosed {
				s.logger.Warn("captive portal listener failed", "error", err)
			}
		}()
	}

	s.logger.Info("dashboard listening", "addr", addr)
	err := srv.ListenAndServe()
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}

// captivePortalRoutes wires up the port-80 mux with handlers for the
// well-known captive portal probe URLs used by iOS, macOS, Android,
// Windows, and Firefox. Any unmatched path falls through to a 302
// redirect pointing the client at the real dashboard.
//
// Platform references:
//   - Apple:   http://captive.apple.com/hotspot-detect.html  (expects
//     "Success" HTML; anything else triggers the portal sheet)
//   - Android: http://connectivitycheck.gstatic.com/generate_204 and
//     /gen_204 (expects 204 No Content; anything else triggers portal)
//   - Windows: http://www.msftconnecttest.com/connecttest.txt (expects
//     the literal text "Microsoft Connect Test"; anything else triggers)
//   - Firefox: http://detectportal.firefox.com/success.txt (expects
//     "success\n"; anything else triggers)
//
// We deliberately fail ALL of these probes by serving a 302 redirect so
// every platform notices the captive portal and opens its in-app browser
// pointed at the dashboard. DHCP option 114 (set by dnsmasq — see
// internal/network) reinforces this on modern clients that honor RFC 8910.
func (s *Server) captivePortalRoutes(mux *http.ServeMux, dashboardPort int) {
	portalURL := fmt.Sprintf("http://10.42.0.1:%d/", dashboardPort)

	redirect := func(w http.ResponseWriter, r *http.Request) {
		// 302 is specifically what captive portal detection looks for.
		// Apple treats any non-"Success" response as a portal, and the
		// redirect body gives us a human-readable fallback if the client
		// follows it directly.
		w.Header().Set("Location", portalURL)
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusFound)
		fmt.Fprintf(w, `<html><body>Redirecting to <a href="%s">%s</a>...</body></html>`,
			portalURL, portalURL)
	}

	// Known probe paths. We register them explicitly rather than relying
	// purely on the catch-all so logs tell us which platform is probing.
	probes := []string{
		"/hotspot-detect.html",         // Apple iOS/macOS
		"/library/test/success.html",   // Apple alt
		"/generate_204",                // Android
		"/gen_204",                     // Android
		"/connecttest.txt",             // Windows
		"/ncsi.txt",                    // Windows NCSI
		"/redirect",                    // Windows
		"/success.txt",                 // Firefox
		"/canonical.html",              // Firefox
	}
	for _, p := range probes {
		path := p
		mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
			s.logger.Debug("captive portal probe", "path", path, "ua", r.UserAgent())
			redirect(w, r)
		})
	}
	// Catch-all: anything else on port 80 also redirects.
	mux.HandleFunc("/", redirect)
}

func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := wsUpgrader.Upgrade(w, r, nil)
	if err != nil {
		s.logger.Error("ws upgrade failed", "error", err)
		return
	}

	s.hub.Register(conn)
	defer s.hub.Unregister(conn)

	// Send initial stats
	st := s.stats.Get()
	data, _ := json.Marshal(wsMessage{Type: "stats", Data: st})
	conn.WriteMessage(websocket.TextMessage, data)

	// Send log history so the client sees recent entries after a refresh
	for _, entry := range s.hub.LogHistory() {
		msg, _ := json.Marshal(wsMessage{Type: "log", Data: entry})
		if err := conn.WriteMessage(websocket.TextMessage, msg); err != nil {
			break
		}
	}

	// Read loop (handles pings and detects disconnects)
	for {
		_, _, err := conn.ReadMessage()
		if err != nil {
			break
		}
	}
}

func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		cfg := s.cfg.Get()
		sanitized := cfg.Sanitized()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"version": s.cfg.Version(),
			"config":  sanitized,
		})

	case http.MethodPut:
		var req struct {
			Version int64         `json:"version"`
			Config  config.Config `json:"config"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		// Preserve real WiFi PSKs when the dashboard sends back masked values
		existing := s.cfg.Get()
		for i, net := range req.Config.WiFi.Networks {
			if net.PSK == "********" && i < len(existing.WiFi.Networks) {
				req.Config.WiFi.Networks[i].PSK = existing.WiFi.Networks[i].PSK
			}
		}
		restarts, err := s.cfg.Update(&req.Config, req.Version)
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"ok":    false,
				"error": err.Error(),
			})
			return
		}
		// Broadcast config change to all connected clients
		updated := s.cfg.Get()
		s.hub.BroadcastConfig(updated.Sanitized(), s.cfg.Version())
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok":             true,
			"version":        s.cfg.Version(),
			"restart_needed": restarts,
		})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleStats(w http.ResponseWriter, r *http.Request) {
	st := s.stats.Get()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(st)
}

func (s *Server) handleFailed(w http.ResponseWriter, r *http.Request) {
	pkts, err := s.buf.FailedPackets(r.Context(), 100)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(pkts)
}

// handleUpdateCheck is the fast, synchronous half of the update flow. It
// queries GitHub for the latest release and reports back whether an update
// is available. No download happens here — the dashboard uses the result
// to decide whether to offer an "Install" button.
func (s *Server) handleUpdateCheck(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	s.logger.Info("manual update check triggered via dashboard")
	result, err := s.updater.Check(r.Context())
	if err != nil {
		s.logger.Warn("update check failed", "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok":              false,
			"error":           err.Error(),
			"current_version": result.CurrentVersion,
			"latest_version":  result.LatestVersion,
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok":              true,
		"available":       result.Available,
		"message":         result.Message,
		"current_version": result.CurrentVersion,
		"latest_version":  result.LatestVersion,
	})
}

// handleUpdateApply kicks off an Apply() in a background goroutine and
// returns immediately. The caller polls /api/update/status for progress.
//
// The goroutine uses context.Background() rather than r.Context() because
// the HTTP request ends the instant we return the 202 — tying the download
// to r.Context() would cancel it mid-stream. A trade-off: if the server
// shuts down mid-download, the tmp file is discarded by the next Apply,
// and the slot swap hasn't happened yet so rollback is unnecessary.
//
// Concurrent apply requests are rejected by Apply() itself via
// ErrApplyInProgress; we surface that to the client so a double-click
// doesn't trigger a second download.
func (s *Server) handleUpdateApply(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s.logger.Info("manual update apply triggered via dashboard")

	go func() {
		if err := s.updater.Apply(context.Background()); err != nil {
			if errors.Is(err, updater.ErrApplyInProgress) {
				// Another Apply raced us — benign, state is already
				// being published by the winner.
				return
			}
			s.logger.Warn("update apply failed", "error", err)
		}
	}()

	// Give the goroutine a tick to take applyMu so the state we return
	// reflects the in-progress apply rather than the previous idle state.
	// Not strictly necessary (client will poll anyway) but avoids a
	// momentarily-stale first render.
	time.Sleep(10 * time.Millisecond)

	state := s.updater.State()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(state)
}

// handleUpdateStatus returns the current updater State for polling clients.
// Cheap — just a mutex-guarded struct copy, no I/O.
func (s *Server) handleUpdateStatus(w http.ResponseWriter, r *http.Request) {
	state := s.updater.State()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(state)
}

func (s *Server) handleReboot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s.logger.Warn("reboot requested via dashboard")
	cmd := exec.Command("sudo", "/sbin/reboot")
	if err := cmd.Start(); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"ok": true, "message": "Reboot command issued."})
}

func (s *Server) handleCrashLogs(w http.ResponseWriter, r *http.Request) {
	boot := r.URL.Query().Get("boot")
	if boot == "" {
		boot = "-1"
	}
	linesStr := r.URL.Query().Get("lines")
	lines := 250
	if linesStr != "" {
		if n, err := strconv.Atoi(linesStr); err == nil && n > 0 {
			lines = n
		}
	}
	cmd := exec.CommandContext(r.Context(), "journalctl",
		"--boot="+boot, "--no-pager", "--lines", strconv.Itoa(lines), "-q")
	out, err := cmd.CombinedOutput()
	if err != nil {
		s.logger.Debug("crashlogs fetch", "boot", boot, "error", err)
		// Return the output anyway — it may contain useful error info from journalctl
		if len(out) == 0 {
			out = []byte("No logs available for boot " + boot + ": " + err.Error())
		}
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Write(out)
}

// allowedRestartServices is the whitelist of services that can be restarted via the dashboard.
var allowedRestartServices = map[string]bool{
	"umbgs":    true,
	"direwolf": true,
	"gpsd":     true,
	"chrony":   true,
}

func (s *Server) handleServiceRestart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	// Extract service name from URL: /api/services/restart/{name}
	name := strings.TrimPrefix(r.URL.Path, "/api/services/restart/")
	if name == "" || !allowedRestartServices[name] {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok": false, "error": "service not allowed: " + name,
		})
		return
	}
	s.logger.Warn("service restart requested", "service", name)

	// When restarting ourselves, send the response first — systemctl restart
	// will SIGTERM this process before CombinedOutput() returns.
	if name == "umbgs" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok": true, "message": "Restart issued for " + name,
		})
		// Flush the response before we die
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		go func() {
			time.Sleep(200 * time.Millisecond)
			exec.Command("sudo", "systemctl", "restart", "umbgs").Start()
		}()
		return
	}

	cmd := exec.CommandContext(r.Context(), "sudo", "systemctl", "restart", name)
	if out, err := cmd.CombinedOutput(); err != nil {
		s.logger.Warn("service restart failed", "service", name, "error", err, "output", string(out))
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok": false, "error": err.Error(),
		})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok": true, "message": "Restart issued for " + name,
	})
}

// handleServiceWorker serves the offline-capable service worker script.
func (s *Server) handleServiceWorker(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/javascript")
	w.Header().Set("Cache-Control", "no-cache")
	w.Write([]byte(serviceWorkerJS))
}

// serviceWorkerJS is the offline-capable service worker script. The cache
// name is bumped whenever a behaviorally-significant change lands so phones
// that previously cached an older HTML don't keep serving it — the activate
// handler deletes every cache whose name differs from the current one.
const serviceWorkerJS = `
var CACHE = 'umbgs-v3';

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(cache) {
      return cache.addAll(['/']);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(n) { return n !== CACHE; })
             .map(function(n) { return caches.delete(n); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function(e) {
  if (e.request.method !== 'GET') return;
  var url = new URL(e.request.url);
  // Never cache API traffic — state/logs/packets must always be fresh,
  // and caching /api/update/status would freeze the progress bar.
  if (url.pathname.indexOf('/api/') === 0 || url.pathname === '/ws') {
    return;
  }
  // For navigation requests (HTML pages), try network first, fall back to cache
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).then(function(resp) {
        var clone = resp.clone();
        caches.open(CACHE).then(function(c) { c.put(e.request, clone); });
        return resp;
      }).catch(function() {
        return caches.match(e.request).then(function(r) { return r || caches.match('/'); });
      })
    );
    return;
  }
  // For other requests, network first with cache fallback
  e.respondWith(
    fetch(e.request).catch(function() { return caches.match(e.request); })
  );
});
`

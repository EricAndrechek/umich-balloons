package dashboard

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
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
	s.mux.HandleFunc("/api/update", s.handleUpdate)
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

// Run starts the HTTP server.
func (s *Server) Run(ctx context.Context) error {
	c := s.cfg.Get()
	addr := fmt.Sprintf(":%d", c.Dashboard.Port)
	srv := &http.Server{
		Addr:    addr,
		Handler: s.mux,
	}

	go func() {
		<-ctx.Done()
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		srv.Shutdown(shutCtx)
	}()

	s.logger.Info("dashboard listening", "addr", addr)
	err := srv.ListenAndServe()
	if err == http.ErrServerClosed {
		return nil
	}
	return err
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

func (s *Server) handleUpdate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	s.logger.Info("manual update check triggered via dashboard")
	err := s.updater.Check(r.Context())
	if err != nil {
		s.logger.Warn("update check failed", "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok":    false,
			"error": err.Error(),
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok":      true,
		"message": "update check complete",
	})
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

const serviceWorkerJS = `
var CACHE = 'umbgs-v1';

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

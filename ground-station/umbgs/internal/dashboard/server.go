package dashboard

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
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
	s.mux.Handle("/", http.FileServer(http.FS(webFS)))
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
		json.NewEncoder(w).Encode(sanitized)

	case http.MethodPut:
		var newCfg config.Config
		if err := json.NewDecoder(r.Body).Decode(&newCfg); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		restarts, err := s.cfg.Update(&newCfg)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ok":             true,
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

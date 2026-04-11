// Package dashboard provides the embedded web dashboard.
package dashboard

import (
	"encoding/json"
	"log/slog"
	"sync"

	"github.com/gorilla/websocket"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

const logHistorySize = 500

// Hub manages WebSocket clients and broadcasts events.
type Hub struct {
	logger  *slog.Logger
	mu      sync.RWMutex
	clients map[*websocket.Conn]struct{}

	// Ring buffer for log history so new connections get recent logs.
	logMu   sync.RWMutex
	logBuf  []types.LogEntry
	logHead int  // next write position
	logFull bool // true once the buffer has wrapped
}

// NewHub creates a new WebSocket hub.
func NewHub(logger *slog.Logger) *Hub {
	return &Hub{
		logger:  logger.With("service", "dashboard-hub"),
		clients: make(map[*websocket.Conn]struct{}),
		logBuf:  make([]types.LogEntry, logHistorySize),
	}
}

// Register adds a WebSocket client.
func (h *Hub) Register(conn *websocket.Conn) {
	h.mu.Lock()
	h.clients[conn] = struct{}{}
	h.mu.Unlock()
	h.logger.Debug("client connected", "clients", len(h.clients))
}

// Unregister removes a WebSocket client.
func (h *Hub) Unregister(conn *websocket.Conn) {
	h.mu.Lock()
	delete(h.clients, conn)
	h.mu.Unlock()
	conn.Close()
	h.logger.Debug("client disconnected")
}

// LogHistory returns a copy of all buffered log entries in chronological order.
func (h *Hub) LogHistory() []types.LogEntry {
	h.logMu.RLock()
	defer h.logMu.RUnlock()

	if !h.logFull {
		out := make([]types.LogEntry, h.logHead)
		copy(out, h.logBuf[:h.logHead])
		return out
	}
	// Full ring: read from head..end then 0..head
	out := make([]types.LogEntry, logHistorySize)
	n := copy(out, h.logBuf[h.logHead:])
	copy(out[n:], h.logBuf[:h.logHead])
	return out
}

// BroadcastPacketEvent sends a packet event to all connected clients.
func (h *Hub) BroadcastPacketEvent(evt types.PacketEvent) {
	h.broadcast(wsMessage{Type: "packet", Data: evt})
}

// BroadcastStats sends system stats to all connected clients.
func (h *Hub) BroadcastStats(stats types.SystemStats) {
	h.broadcast(wsMessage{Type: "stats", Data: stats})
}

// BroadcastConfig sends a config update to all connected clients.
func (h *Hub) BroadcastConfig(cfg interface{}, version int64) {
	h.broadcast(wsMessage{
		Type: "config",
		Data: map[string]interface{}{
			"config":  cfg,
			"version": version,
		},
	})
}

// BroadcastLog stores entry in the ring buffer and sends it to all clients.
func (h *Hub) BroadcastLog(entry types.LogEntry) {
	// Store in ring buffer
	h.logMu.Lock()
	h.logBuf[h.logHead] = entry
	h.logHead++
	if h.logHead >= logHistorySize {
		h.logHead = 0
		h.logFull = true
	}
	h.logMu.Unlock()

	h.broadcast(wsMessage{Type: "log", Data: entry})
}

func (h *Hub) broadcast(msg wsMessage) {
	data, err := json.Marshal(msg)
	if err != nil {
		h.logger.Error("failed to marshal ws message", "error", err)
		return
	}

	h.mu.RLock()
	defer h.mu.RUnlock()

	for conn := range h.clients {
		if err := conn.WriteMessage(websocket.TextMessage, data); err != nil {
			h.logger.Debug("failed to write to client, will remove", "error", err)
			go h.Unregister(conn)
		}
	}
}

type wsMessage struct {
	Type string      `json:"type"`
	Data interface{} `json:"data"`
}

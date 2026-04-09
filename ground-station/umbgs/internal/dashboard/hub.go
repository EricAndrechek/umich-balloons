// Package dashboard provides the embedded web dashboard.
package dashboard

import (
	"encoding/json"
	"log/slog"
	"sync"

	"github.com/gorilla/websocket"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// Hub manages WebSocket clients and broadcasts events.
type Hub struct {
	logger  *slog.Logger
	mu      sync.RWMutex
	clients map[*websocket.Conn]struct{}
}

// NewHub creates a new WebSocket hub.
func NewHub(logger *slog.Logger) *Hub {
	return &Hub{
		logger:  logger.With("service", "dashboard-hub"),
		clients: make(map[*websocket.Conn]struct{}),
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

// BroadcastPacketEvent sends a packet event to all connected clients.
func (h *Hub) BroadcastPacketEvent(evt types.PacketEvent) {
	msg := wsMessage{
		Type: "packet",
		Data: evt,
	}
	h.broadcast(msg)
}

// BroadcastStats sends system stats to all connected clients.
func (h *Hub) BroadcastStats(stats types.SystemStats) {
	msg := wsMessage{
		Type: "stats",
		Data: stats,
	}
	h.broadcast(msg)
}

// BroadcastLog sends a log entry to all connected clients.
func (h *Hub) BroadcastLog(entry types.LogEntry) {
	msg := wsMessage{
		Type: "log",
		Data: entry,
	}
	h.broadcast(msg)
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

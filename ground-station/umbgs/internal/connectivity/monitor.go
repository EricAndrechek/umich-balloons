// Package connectivity monitors network state via NetworkManager over D-Bus.
package connectivity

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/godbus/dbus/v5"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

const (
	nmBus       = "org.freedesktop.NetworkManager"
	nmPath      = "/org/freedesktop/NetworkManager"
	nmInterface = "org.freedesktop.NetworkManager"
)

// NM connectivity states.
const (
	nmConnFull    uint32 = 4
	nmConnLimited uint32 = 3
	nmConnPortal  uint32 = 2
	nmConnNone    uint32 = 1
	nmConnUnknown uint32 = 0
)

// Monitor tracks network connectivity and interface state.
type Monitor struct {
	logger *slog.Logger

	mu     sync.RWMutex
	online bool
	status types.NetworkStatus
}

// NewMonitor creates a connectivity monitor.
func NewMonitor(logger *slog.Logger) *Monitor {
	return &Monitor{logger: logger.With("service", "connectivity")}
}

// Online returns whether the station has internet connectivity.
func (m *Monitor) Online() bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.online
}

// Status returns the current network status.
func (m *Monitor) Status() types.NetworkStatus {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.status
}

// Run polls NetworkManager connectivity state.
func (m *Monitor) Run(ctx context.Context) error {
	conn, err := dbus.SystemBus()
	if err != nil {
		m.logger.Warn("D-Bus unavailable, assuming online", "error", err)
		return m.pollFallback(ctx)
	}
	defer conn.Close()

	m.logger.Info("connected to D-Bus, monitoring NetworkManager")

	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	m.checkNM(conn)

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			m.checkNM(conn)
		}
	}
}

func (m *Monitor) checkNM(conn *dbus.Conn) {
	obj := conn.Object(nmBus, dbus.ObjectPath(nmPath))

	variant, err := obj.GetProperty(nmInterface + ".Connectivity")
	if err != nil {
		m.logger.Debug("failed to get NM connectivity", "error", err)
		return
	}

	state, ok := variant.Value().(uint32)
	if !ok {
		return
	}

	online := state == nmConnFull
	connStr := "none"
	switch state {
	case nmConnFull:
		connStr = "full"
	case nmConnLimited:
		connStr = "limited"
	case nmConnPortal:
		connStr = "portal"
	}

	m.mu.Lock()
	m.online = online
	m.status.Connectivity = connStr
	m.mu.Unlock()

	m.logger.Debug("connectivity check", "state", connStr, "online", online)
}

// pollFallback assumes online when D-Bus is unavailable.
func (m *Monitor) pollFallback(ctx context.Context) error {
	m.mu.Lock()
	m.online = true
	m.status.Connectivity = "unknown"
	m.mu.Unlock()

	<-ctx.Done()
	return ctx.Err()
}

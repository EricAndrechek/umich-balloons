// Package connectivity monitors network state via NetworkManager over D-Bus.
package connectivity

import (
	"context"
	"fmt"
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
	// Do NOT close the shared system bus — it's a process-wide singleton.
	// Closing it breaks all other D-Bus users in the process.

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

	// Gather interface details
	ifaces := m.getInterfaces(conn)

	m.mu.Lock()
	m.online = online
	m.status.Connectivity = connStr
	m.status.Interfaces = ifaces
	m.mu.Unlock()

	m.logger.Debug("connectivity check", "state", connStr, "online", online, "interfaces", len(ifaces))
}

// getInterfaces reads active NM device info via D-Bus.
func (m *Monitor) getInterfaces(conn *dbus.Conn) []types.NetInterface {
	obj := conn.Object(nmBus, dbus.ObjectPath(nmPath))

	var devicePaths []dbus.ObjectPath
	if err := obj.Call(nmInterface+".GetAllDevices", 0).Store(&devicePaths); err != nil {
		m.logger.Debug("failed to get NM devices", "error", err)
		return nil
	}

	var ifaces []types.NetInterface
	for _, dp := range devicePaths {
		dev := conn.Object(nmBus, dp)

		// Get device type (1=ethernet, 2=wifi, 14=generic, etc.)
		dtVar, err := dev.GetProperty("org.freedesktop.NetworkManager.Device.DeviceType")
		if err != nil {
			continue
		}
		devType, _ := dtVar.Value().(uint32)
		if devType != 1 && devType != 2 { // only ethernet and wifi
			continue
		}

		// Get interface name
		ifVar, _ := dev.GetProperty("org.freedesktop.NetworkManager.Device.Interface")
		ifName, _ := ifVar.Value().(string)

		// Get device state (100 = activated)
		stVar, _ := dev.GetProperty("org.freedesktop.NetworkManager.Device.State")
		devState, _ := stVar.Value().(uint32)

		ni := types.NetInterface{
			Name:  ifName,
			State: "disconnected",
		}

		if devType == 1 {
			ni.Type = "ethernet"
		} else {
			ni.Type = "wifi"
		}

		if devState == 100 { // NM_DEVICE_STATE_ACTIVATED
			ni.State = "connected"

			// Get IP address from Ip4Address (simpler uint32 property) as fallback,
			// and try AddressData first for the string form.
			ip4CfgVar, _ := dev.GetProperty("org.freedesktop.NetworkManager.Device.Ip4Config")
			if ip4Path, ok := ip4CfgVar.Value().(dbus.ObjectPath); ok && ip4Path != "/" {
				ip4Obj := conn.Object(nmBus, ip4Path)
				addrVar, _ := ip4Obj.GetProperty("org.freedesktop.NetworkManager.IP4Config.AddressData")
				ni.IP = extractIPFromAddressData(addrVar)
			}
			// Fallback: parse Ip4Address uint32 if AddressData didn't yield an IP
			if ni.IP == "" {
				ip4Var, _ := dev.GetProperty("org.freedesktop.NetworkManager.Device.Ip4Address")
				if ip4uint, ok := ip4Var.Value().(uint32); ok && ip4uint != 0 {
					ni.IP = fmt.Sprintf("%d.%d.%d.%d",
						ip4uint&0xFF, (ip4uint>>8)&0xFF, (ip4uint>>16)&0xFF, (ip4uint>>24)&0xFF)
				}
			}

			// Get WiFi-specific info (SSID, signal)
			if devType == 2 {
				apVar, _ := dev.GetProperty("org.freedesktop.NetworkManager.Device.Wireless.ActiveAccessPoint")
				if apPath, ok := apVar.Value().(dbus.ObjectPath); ok && apPath != "/" {
					apObj := conn.Object(nmBus, apPath)
					ssidVar, _ := apObj.GetProperty("org.freedesktop.NetworkManager.AccessPoint.Ssid")
					if ssidBytes, ok := ssidVar.Value().([]byte); ok {
						ni.SSID = string(ssidBytes)
					}
					sigVar, _ := apObj.GetProperty("org.freedesktop.NetworkManager.AccessPoint.Strength")
					if sig, ok := sigVar.Value().(uint8); ok {
						ni.Signal = int(sig)
					}
				}
			}
		}

		ifaces = append(ifaces, ni)
	}
	return ifaces
}

// extractIPFromAddressData tries to pull an IP string from the NM AddressData property.
// The D-Bus type is aa{sv}; godbus may decode it as []map[string]dbus.Variant or []interface{}.
func extractIPFromAddressData(v dbus.Variant) string {
	// Try direct type first
	if addrs, ok := v.Value().([]map[string]dbus.Variant); ok && len(addrs) > 0 {
		if addrV, ok := addrs[0]["address"]; ok {
			if s, ok := addrV.Value().(string); ok {
				return s
			}
		}
	}
	// godbus often returns []interface{} for arrays
	if arr, ok := v.Value().([]interface{}); ok && len(arr) > 0 {
		if m, ok := arr[0].(map[string]dbus.Variant); ok {
			if addrV, ok := m["address"]; ok {
				if s, ok := addrV.Value().(string); ok {
					return s
				}
			}
		}
	}
	return ""
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

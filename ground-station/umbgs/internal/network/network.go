// Package network manages WiFi configuration sync via NetworkManager D-Bus.
package network

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/godbus/dbus/v5"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
)

const (
	nmSettingsBus   = "org.freedesktop.NetworkManager"
	nmSettingsPath  = "/org/freedesktop/NetworkManager/Settings"
	nmSettingsIface = "org.freedesktop.NetworkManager.Settings"
	nmConnIface     = "org.freedesktop.NetworkManager.Settings.Connection"
)

// Manager syncs WiFi networks from config to NetworkManager and manages AP mode.
type Manager struct {
	cfg    *config.Manager
	logger *slog.Logger
}

// NewManager creates a network manager.
func NewManager(cfg *config.Manager, logger *slog.Logger) *Manager {
	return &Manager{cfg: cfg, logger: logger.With("service", "network")}
}

// Run syncs WiFi config on startup and watches for changes.
func (m *Manager) Run(ctx context.Context) error {
	if err := m.syncWiFi(); err != nil {
		m.logger.Warn("initial WiFi sync failed", "error", err)
	}

	if err := m.ensureAPConnection(); err != nil {
		m.logger.Warn("AP connection setup failed", "error", err)
	}

	// Re-sync periodically in case config changes
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if err := m.syncWiFi(); err != nil {
				m.logger.Warn("WiFi sync failed", "error", err)
			}
		}
	}
}

// syncWiFi creates/updates NetworkManager connections for configured WiFi networks.
func (m *Manager) syncWiFi() error {
	c := m.cfg.Get()
	if len(c.WiFi.Networks) == 0 {
		m.logger.Debug("no WiFi networks configured")
		return nil
	}

	conn, err := dbus.SystemBus()
	if err != nil {
		return fmt.Errorf("D-Bus: %w", err)
	}
	// Do NOT close shared system bus — it's a process-wide singleton.

	for _, net := range c.WiFi.Networks {
		if net.SSID == "" || net.PSK == "" {
			continue
		}
		if err := m.addOrUpdateConnection(conn, net.SSID, net.PSK); err != nil {
			m.logger.Warn("failed to sync WiFi network", "ssid", net.SSID, "error", err)
		} else {
			m.logger.Info("WiFi network synced", "ssid", net.SSID)
		}
	}
	return nil
}

// findConnectionByID looks up an existing NM connection by its "id" field.
func (m *Manager) findConnectionByID(conn *dbus.Conn, id string) (dbus.ObjectPath, bool) {
	obj := conn.Object(nmSettingsBus, dbus.ObjectPath(nmSettingsPath))
	var paths []dbus.ObjectPath
	if err := obj.Call(nmSettingsIface+".ListConnections", 0).Store(&paths); err != nil {
		return "", false
	}
	for _, p := range paths {
		cObj := conn.Object(nmSettingsBus, p)
		var settings map[string]map[string]dbus.Variant
		if err := cObj.Call(nmConnIface+".GetSettings", 0).Store(&settings); err != nil {
			continue
		}
		if connSec, ok := settings["connection"]; ok {
			if idV, ok := connSec["id"]; ok {
				if idV.Value().(string) == id {
					return p, true
				}
			}
		}
	}
	return "", false
}

func (m *Manager) addOrUpdateConnection(conn *dbus.Conn, ssid, psk string) error {
	connID := "umbgs-" + ssid
	settings := map[string]map[string]dbus.Variant{
		"connection": {
			"id":          dbus.MakeVariant(connID),
			"type":        dbus.MakeVariant("802-11-wireless"),
			"autoconnect": dbus.MakeVariant(true),
		},
		"802-11-wireless": {
			"ssid": dbus.MakeVariant([]byte(ssid)),
			"mode": dbus.MakeVariant("infrastructure"),
		},
		"802-11-wireless-security": {
			"key-mgmt": dbus.MakeVariant("wpa-psk"),
			"psk":      dbus.MakeVariant(psk),
		},
		"ipv4": {
			"method": dbus.MakeVariant("auto"),
		},
		"ipv6": {
			"method": dbus.MakeVariant("auto"),
		},
	}

	if existing, ok := m.findConnectionByID(conn, connID); ok {
		cObj := conn.Object(nmSettingsBus, existing)
		call := cObj.Call(nmConnIface+".Update", 0, settings)
		if call.Err != nil {
			return fmt.Errorf("update connection: %w", call.Err)
		}
		m.logger.Debug("updated existing connection", "id", connID)
		return nil
	}

	obj := conn.Object(nmSettingsBus, dbus.ObjectPath(nmSettingsPath))
	call := obj.Call(nmSettingsIface+".AddConnection", 0, settings)
	if call.Err != nil {
		return fmt.Errorf("add connection: %w", call.Err)
	}
	return nil
}

// ensureAPConnection creates a NetworkManager hotspot connection for fallback.
func (m *Manager) ensureAPConnection() error {
	c := m.cfg.Get()
	callsign := c.UploaderCallsign()
	if callsign == "" {
		callsign = "UMBGroundStation"
	}

	apSSID := "UMB-" + callsign
	connID := "umbgs-hotspot"

	conn, err := dbus.SystemBus()
	if err != nil {
		return fmt.Errorf("D-Bus: %w", err)
	}
	// Do NOT close shared system bus.

	// Hotspot is configured as a last-resort autoconnect fallback.
	//
	// NM's can_auto_connect (src/core/devices/wifi/nm-device-wifi.c) always
	// allows AP mode profiles in the autoconnect loop, so autoconnect=true
	// on a mode=ap profile is a supported, documented NM pattern. The very
	// negative autoconnect-priority ensures any configured infrastructure
	// WiFi wins when one is in range — the hotspot only comes up when
	// every other autoconnect candidate has been ruled out.
	//
	// autoconnect-retries=0 means "retry forever": without this NM gives up
	// after the default 4 failures, which defeats the entire fallback design
	// for a chase vehicle that may spend hours outside any known SSID.
	//
	// Previously this was autoconnect=false with no explicit activation path,
	// so the profile existed in NM but nothing ever brought it up — the
	// hotspot SSID never appeared to nearby phones in the field.
	settings := map[string]map[string]dbus.Variant{
		"connection": {
			"id":                   dbus.MakeVariant(connID),
			"type":                 dbus.MakeVariant("802-11-wireless"),
			"autoconnect":          dbus.MakeVariant(true),
			"autoconnect-priority": dbus.MakeVariant(int32(-999)),
			"autoconnect-retries":  dbus.MakeVariant(int32(0)),
		},
		"802-11-wireless": {
			"ssid": dbus.MakeVariant([]byte(apSSID)),
			"mode": dbus.MakeVariant("ap"),
			"band": dbus.MakeVariant("bg"),
		},
		"ipv4": {
			"method": dbus.MakeVariant("shared"),
		},
		"ipv6": {
			"method": dbus.MakeVariant("ignore"),
		},
	}

	if existing, ok := m.findConnectionByID(conn, connID); ok {
		cObj := conn.Object(nmSettingsBus, existing)
		call := cObj.Call(nmConnIface+".Update", 0, settings)
		if call.Err != nil {
			return fmt.Errorf("update hotspot: %w", call.Err)
		}
		m.logger.Debug("updated existing hotspot connection", "ssid", apSSID)
		return nil
	}

	obj := conn.Object(nmSettingsBus, dbus.ObjectPath(nmSettingsPath))
	call := obj.Call(nmSettingsIface+".AddConnection", 0, settings)
	if call.Err != nil {
		return fmt.Errorf("add hotspot: %w", call.Err)
	}
	m.logger.Info("AP hotspot connection created", "ssid", apSSID)
	return nil
}

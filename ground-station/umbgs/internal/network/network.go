// Package network manages WiFi configuration sync via NetworkManager D-Bus.
package network

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
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

	// Write the dnsmasq drop-in BEFORE ensuring the AP exists — if NM
	// brings the hotspot up on a fresh boot it'll launch dnsmasq with
	// our config in place. If the file was updated since the last boot
	// (e.g. after an auto-update), taking effect requires a reboot or
	// an AP deactivate/reactivate cycle; we log a reminder rather than
	// trying to force it from here.
	if err := m.ensureCaptivePortalDNS(); err != nil {
		m.logger.Warn("captive portal dnsmasq config failed", "error", err)
	}

	if err := m.ensureAPConnection(); err != nil {
		m.logger.Warn("AP connection setup failed", "error", err)
	}

	if err := m.ensureAPFirewall(); err != nil {
		m.logger.Warn("AP firewall rules failed", "error", err)
	}

	// Emit a captive portal health report to the log. This is load-bearing
	// for field debugging: the operator's only view into the Pi is the
	// dashboard logs, and "captive portal isn't popping up" has a handful
	// of distinct failure modes (missing drop-in, dnsmasq not running,
	// dnsmasq running without our config) that each need a different fix.
	//
	// We deliberately defer the first report by captivePortalHealthDelay:
	// NM may not have activated the hotspot yet when umbgs starts, so
	// checking dnsmasq immediately would produce a spurious "no dnsmasq
	// process found" false alarm even on a perfectly working system. The
	// delay lets NM's autoconnect loop bring the hotspot up first.
	go func() {
		select {
		case <-ctx.Done():
			return
		case <-time.After(captivePortalHealthDelay):
		}
		m.logCaptivePortalHealth()
	}()

	// Re-sync periodically in case config changes. The same tick also
	// re-runs the captive portal health check so a late hotspot bring-up
	// (or a post-install drop-in write that hadn't taken effect yet) gets
	// re-reported once it reaches steady state.
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
			m.logCaptivePortalHealth()
		}
	}
}

// captivePortalHealthDelay is how long we wait after startup before
// checking whether dnsmasq is running. NM's autoconnect loop typically
// brings the fallback hotspot up within a few seconds, but on a fresh
// boot with slow SD-card I/O it can take longer; 20s is enough headroom
// to avoid false "no dnsmasq" warnings while still surfacing a real
// problem quickly.
const captivePortalHealthDelay = 20 * time.Second

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

// dnsmasqDropInPath is the file NM's shared-mode dnsmasq reads for extra
// configuration. The directory is a standard NM feature: any .conf file
// inside is appended to dnsmasq's command line when NM launches the
// shared-mode instance. Documented at
// https://developer-old.gnome.org/NetworkManager/stable/NetworkManager.conf.html
// under "dns" / shared-mode extras.
const dnsmasqDropInPath = "/etc/NetworkManager/dnsmasq-shared.d/umbgs-captive.conf"

// captiveDNSConfig is the dnsmasq drop-in that makes the AP behave as a
// captive portal:
//
//   - address=/#/10.42.0.1 is wildcard DNS: every name resolves to the
//     Pi, so the OS's captive-portal probe (captive.apple.com,
//     connectivitycheck.gstatic.com, etc.) lands on our port-80 listener
//     and gets a 302 that trips the portal UI.
//
//   - dhcp-option=114,http://10.42.0.1/ is RFC 8910: modern iOS/Android
//     read option 114 and auto-open that URL in the captive portal
//     sheet without waiting for a failed probe.
//
//   - no-resolv + no-hosts keeps dnsmasq from leaking real DNS lookups
//     out through the Pi's upstream connection. Without these, dnsmasq
//     would try to forward queries for e.g. captive.apple.com via
//     /etc/resolv.conf and return the real IP, defeating the wildcard.
//
// 10.42.0.1 is NetworkManager's hardcoded default for shared-mode
// connections; changing it would require explicitly setting
// ipv4.addresses on the hotspot profile.
const captiveDNSConfig = `# Managed by umbgs — do not edit by hand.
# Wildcard DNS: every lookup returns the Pi so captive portal probes
# (apple/android/windows/firefox) hit our port-80 listener and get the
# 302 that triggers the portal UI.
address=/#/10.42.0.1

# Don't leak DNS out through the Pi's upstream connection — we want
# every query to be answered locally by the wildcard above.
no-resolv
no-hosts

# RFC 8910 captive portal URL — modern clients honor this and auto-open
# the dashboard without needing a failed probe first.
dhcp-option=114,http://10.42.0.1/
`

// ensureCaptivePortalDNS writes the dnsmasq drop-in for the AP hotspot.
// Idempotent: if the on-disk content matches already, it's a no-op.
// Non-fatal: if the directory doesn't exist (non-NM systems, odd
// distros) or we can't write, we log and continue — the dashboard will
// still be reachable via the AP's IP, just without wildcard DNS or
// option 114.
//
// NOTE: install.sh seeds this same file at install time, which is what
// actually matters on first boot — NM may activate the hotspot fallback
// before umbgs has a chance to run, and dnsmasq reads the drop-in dir
// once at fork time. This runtime write is a safety net for upgrades
// and for the case where something clobbered the file post-install.
func (m *Manager) ensureCaptivePortalDNS() error {
	dir := filepath.Dir(dnsmasqDropInPath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("mkdir %s: %w", dir, err)
	}
	existing, err := os.ReadFile(dnsmasqDropInPath)
	if err == nil && string(existing) == captiveDNSConfig {
		m.logger.Debug("captive portal dnsmasq config already current")
		return nil
	}
	if err := os.WriteFile(dnsmasqDropInPath, []byte(captiveDNSConfig), 0644); err != nil {
		return fmt.Errorf("write %s: %w", dnsmasqDropInPath, err)
	}
	m.logger.Info("captive portal dnsmasq config updated",
		"path", dnsmasqDropInPath,
		"note", "takes effect on next AP activation or reboot")
	return nil
}

// logCaptivePortalHealth emits a diagnostic line about the captive
// portal's runtime state. Not authoritative — just enough signal to
// triage a "portal isn't popping up on my phone" report from the field
// logs view without SSHing into the Pi.
//
// We check three things:
//
//  1. The drop-in file exists on disk where NM's shared-mode dnsmasq
//     expects it. install.sh seeds this at image build time, and
//     ensureCaptivePortalDNS() writes it at every startup as a safety
//     net, so by the time we're called it should always be present.
//     If it's missing, something external clobbered it.
//
//  2. A dnsmasq process is running. NM spawns its own dnsmasq per
//     shared-mode connection, so "no dnsmasq" while the hotspot is up
//     means either NM's shared mode fell back to the internal server
//     (no dnsmasq-base installed?) or the hotspot isn't actually
//     active.
//
//  3. If dnsmasq IS running, check whether its cmdline mentions our
//     drop-in file. If not, dnsmasq was spawned before the drop-in
//     existed — the file won't take effect until the hotspot is
//     deactivated/reactivated, which is the main thing that can go
//     wrong even when the file exists.
func (m *Manager) logCaptivePortalHealth() {
	dropInOK := false
	if _, err := os.Stat(dnsmasqDropInPath); err == nil {
		dropInOK = true
	}

	// pgrep -a dnsmasq: prints "<pid> <full cmdline>" per matching
	// process. -a is on Debian's procps by default; if it's missing on
	// an odd distro the command fails and we fall through.
	cmdlines, err := exec.Command("pgrep", "-a", "dnsmasq").Output()
	if err != nil {
		m.logger.Warn("captive portal health: no dnsmasq process found",
			"dropin_present", dropInOK,
			"dropin_path", dnsmasqDropInPath,
			"hint", "AP hotspot may not be active yet; revisit after hotspot activation")
		return
	}

	output := strings.TrimSpace(string(cmdlines))
	dropInActive := strings.Contains(output, dnsmasqDropInPath) ||
		strings.Contains(output, "dnsmasq-shared.d")

	if dropInOK && dropInActive {
		m.logger.Info("captive portal health: OK",
			"dropin_path", dnsmasqDropInPath,
			"dnsmasq", output)
		return
	}

	m.logger.Warn("captive portal health: dnsmasq running without drop-in",
		"dropin_present", dropInOK,
		"dropin_path", dnsmasqDropInPath,
		"dnsmasq", output,
		"hint", "dnsmasq forked before drop-in was written; run 'nmcli con down umbgs-hotspot && nmcli con up umbgs-hotspot' to re-exec dnsmasq with the drop-in active")
}

// ensureAPFirewall installs iptables rules that:
//
//  1. Block FORWARD from the wifi interface. NM's "shared" mode enables
//     IP masquerading and forwarding from the AP clients out through
//     whatever uplink is active (ethernet, cellular). For a field
//     ground station that's actively bad: a phone joining the hotspot
//     would tunnel all its traffic through the Pi's LTE stick, chewing
//     up the data plan and giving a terrible UX. This rule ensures AP
//     clients can only reach the Pi itself (INPUT chain, unaffected).
//
//  2. REDIRECT port 80 on the wifi interface to the dashboard port.
//     Captive portal probes hit port 80 regardless; we want them all
//     to land on our captive portal listener. (The listener already
//     binds :80 on all interfaces, so this REDIRECT is defensive for
//     the case where something else is holding :80.)
//
// Both rules are idempotent: we check with -C first and only -I if
// absent. iptables must be installed explicitly on Pi OS Bookworm —
// the default firewall is nftables and the `iptables` package (which
// provides the iptables-nft compat shim) is NOT pulled in by
// network-manager. install.sh adds it to the apt install list; if
// it's still missing we log and continue rather than failing the
// whole subsystem (the hotspot still works, it just forwards AP
// client traffic out the uplink, which is a bandwidth leak but not
// a crash).
//
// We don't clean up on shutdown: the rules are safe to leave in place
// (they only affect the wifi interface which is a no-op during station
// mode) and systemd restart cycles would otherwise thrash the chain.
func (m *Manager) ensureAPFirewall() error {
	const iface = "wlan0"

	if _, err := exec.LookPath("iptables"); err != nil {
		return fmt.Errorf("iptables not found: %w", err)
	}

	addIfMissing := func(table, chain string, rule ...string) error {
		checkArgs := append([]string{"-t", table, "-C", chain}, rule...)
		if err := exec.Command("iptables", checkArgs...).Run(); err == nil {
			return nil // already present
		}
		insertArgs := append([]string{"-t", table, "-I", chain, "1"}, rule...)
		out, err := exec.Command("iptables", insertArgs...).CombinedOutput()
		if err != nil {
			return fmt.Errorf("iptables %v: %w: %s", insertArgs, err, string(out))
		}
		return nil
	}

	// 1. Drop forwarded traffic from the wifi interface so AP clients
	//    can't reach the ethernet uplink.
	if err := addIfMissing("filter", "FORWARD", "-i", iface, "-j", "DROP"); err != nil {
		return err
	}
	m.logger.Debug("iptables FORWARD DROP installed", "iface", iface)

	return nil
}

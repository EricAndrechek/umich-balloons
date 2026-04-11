package direwolf

import (
	"strings"
	"testing"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
)

// TestRenderConfig_IGateEnabled verifies the generated direwolf.conf contains
// everything we need for an RX-only, mobile APRS-IS client:
//
//   - MYCALL set to the full callsign-SSID
//   - GPSD enabled (for live TBEACON position)
//   - IGFILTER p/N0CALL to block the firehose of inbound APRS-IS traffic
//   - TBEACON EVERY=2 (2 minutes) — frequent enough for aprs.fi to draw a
//     continuous trail for the chase vehicle
//
// Direwolf's time format for beacon intervals is "minutes" or "minutes:seconds"
// (see parse_interval in direwolf's src/config.c), so EVERY=2 = 2 minutes.
func TestRenderConfig_IGateEnabled(t *testing.T) {
	cfg := config.Defaults()
	cfg.Callsign = "KD8CJT"
	cfg.SSID = 9

	out, err := renderConfig(cfg)
	if err != nil {
		t.Fatalf("renderConfig: %v", err)
	}

	mustContain(t, out, "MYCALL KD8CJT-9")
	mustContain(t, out, "GPSD")
	mustContain(t, out, "IGSERVER noam.aprs2.net")
	mustContain(t, out, "IGLOGIN KD8CJT-9 19121")

	// The whole point of this test: source-side filter must be present.
	mustContain(t, out, "IGFILTER p/N0CALL")

	// TBEACON line must use the 2-minute rate and the car/R symbol.
	mustContain(t, out, "EVERY=2 ")
	mustContain(t, out, `SYMBOL="car"`)
	mustContain(t, out, "OVERLAY=R")
	mustContain(t, out, `comment="UMich Balloons Ground Station"`)

	// Regression guards: we must never ship the old 30-minute beacon rate
	// (too slow to draw a trail) or forget the IGFILTER directive.
	if strings.Contains(out, "EVERY=30") {
		t.Error("regression: EVERY=30 (30-minute beacon rate) must not be used for the mobile chase vehicle")
	}
	if !strings.Contains(out, "IGFILTER") {
		t.Error("regression: IGFILTER directive missing — APRS-IS firehose will come through")
	}
}

// TestRenderConfig_NoIGate ensures we do not emit any iGate directives when
// the user has cleared the IGServer field (e.g. to run RF-only). The whole
// IG block is gated behind `{{- if .IGServer}}`, so dropping the server
// should drop the filter, login, and beacon along with it.
func TestRenderConfig_NoIGate(t *testing.T) {
	cfg := config.Defaults()
	cfg.Callsign = "KD8CJT"
	cfg.APRS.IGServer = ""

	out, err := renderConfig(cfg)
	if err != nil {
		t.Fatalf("renderConfig: %v", err)
	}

	for _, forbidden := range []string{"IGSERVER", "IGLOGIN", "IGFILTER", "IGTXLIMIT", "TBEACON"} {
		if strings.Contains(out, forbidden) {
			t.Errorf("unexpected %q in rendered config with empty IGServer", forbidden)
		}
	}

	// But the core modem config must still be present.
	mustContain(t, out, "MYCALL KD8CJT-9")
	mustContain(t, out, "ADEVICE stdin null")
	mustContain(t, out, "GPSD")
}

// TestRenderConfig_CustomComment verifies the user's beacon_comment flows
// through and is quoted correctly in the TBEACON line.
func TestRenderConfig_CustomComment(t *testing.T) {
	cfg := config.Defaults()
	cfg.Callsign = "KD8CJT"
	cfg.APRS.BeaconComment = "Chase vehicle 1"

	out, err := renderConfig(cfg)
	if err != nil {
		t.Fatalf("renderConfig: %v", err)
	}

	mustContain(t, out, `comment="Chase vehicle 1"`)
}

// TestRenderConfig_CallsignCaseAndSSID verifies the MYCALL/IGLOGIN lines
// always use uppercase callsign and include the SSID — a lowercase
// callsign in config.yaml must not leak into direwolf.conf.
func TestRenderConfig_CallsignCaseAndSSID(t *testing.T) {
	cfg := config.Defaults()
	cfg.Callsign = "kd8cjt" // lowercase on purpose
	cfg.SSID = 9

	out, err := renderConfig(cfg)
	if err != nil {
		t.Fatalf("renderConfig: %v", err)
	}

	mustContain(t, out, "MYCALL KD8CJT-9")
	mustContain(t, out, "IGLOGIN KD8CJT-9")
	if strings.Contains(out, "kd8cjt") {
		t.Error("lowercase callsign leaked into rendered config")
	}
}

func mustContain(t *testing.T, haystack, needle string) {
	t.Helper()
	if !strings.Contains(haystack, needle) {
		t.Errorf("rendered config missing %q\n--- full output ---\n%s", needle, haystack)
	}
}

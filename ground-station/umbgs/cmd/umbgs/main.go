package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/aprs"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/buffer"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/connectivity"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/dashboard"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/direwolf"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/gps"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/led"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/lora"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/network"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/system"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/updater"
	uploaderPkg "github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/uploader"
)

var version = "dev"

const (
	logDir      = "/data/logs"
	logFile     = "/data/logs/umbgs.log"
	maxLogSize  = 10 * 1024 * 1024 // 10 MB
	snapshotDir = "/boot/firmware"
)

func main() {
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()
	if *showVersion {
		fmt.Println(version)
		os.Exit(0)
	}

	// Setup log directory
	os.MkdirAll(logDir, 0755)

	// Open persistent log file
	lf, err := openLogFile(logFile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "warning: cannot open log file %s: %v\n", logFile, err)
	}

	// Setup structured logging to both stdout and log file
	logLevel := new(slog.LevelVar)
	logLevel.Set(slog.LevelInfo)
	var logWriter io.Writer
	if lf != nil {
		logWriter = io.MultiWriter(os.Stdout, lf)
	} else {
		logWriter = os.Stdout
	}
	logger := slog.New(slog.NewJSONHandler(logWriter, &slog.HandlerOptions{Level: logLevel}))
	slog.SetDefault(logger)

	logger.Info("umbgs starting", "version", version, "pid", os.Getpid())

	// Load config
	cfg, cfgPath, err := config.Load()
	if err != nil {
		logger.Error("failed to load config", "error", err)
		os.Exit(1)
	}
	configured := cfg.Configured()
	if configured {
		logger.Info("config loaded", "path", cfgPath, "callsign", cfg.UploaderCallsign())
	} else {
		logger.Warn("callsign not configured — starting in setup mode (dashboard + network only)")
	}

	// Set log level from config
	switch cfg.LogLevel {
	case "debug":
		logLevel.Set(slog.LevelDebug)
	case "warn":
		logLevel.Set(slog.LevelWarn)
	case "error":
		logLevel.Set(slog.LevelError)
	}

	cfgMgr := config.NewManager(cfg, cfgPath)

	// Direwolf runner (replaces standalone direwolf.service)
	dwRunner := direwolf.NewRunner(cfgMgr, logger)

	// Open buffer database
	buf, err := buffer.Open(logger)
	if err != nil {
		logger.Error("failed to open buffer database", "error", err)
		os.Exit(1)
	}
	defer buf.Close()

	// Create subsystems
	ledCtrl := led.NewController(logger)
	connMon := connectivity.NewMonitor(logger)
	gpsReporter := gps.NewReporter(cfgMgr, logger)
	sysStats := system.NewStats(version, logger)
	hub := dashboard.NewHub(logger)
	logAgg := dashboard.NewLogAggregator(hub, logger)
	netMgr := network.NewManager(cfgMgr, logger)
	upd := updater.NewUpdater(cfgMgr, version, logger)

	// Packet pipeline
	pktChan := make(chan types.Packet, 64)
	evtChan := make(chan types.PacketEvent, 64)
	aprsListener := aprs.NewListener(cfgMgr, pktChan, logger)
	loraReader := lora.NewReader(cfgMgr, pktChan, logger)
	ul := uploaderPkg.New(cfgMgr, buf, connMon, pktChan, evtChan, logger)

	// Dashboard server
	dashSrv := dashboard.NewServer(cfgMgr, hub, sysStats, buf, upd, logger)

	// Context for graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	// Debug snapshot on SIGUSR1
	snapshotCh := make(chan os.Signal, 1)
	signal.Notify(snapshotCh, syscall.SIGUSR1)
	go func() {
		for range snapshotCh {
			if err := writeDebugSnapshot(logger); err != nil {
				logger.Error("failed to write debug snapshot", "error", err)
			}
		}
	}()

	var wg sync.WaitGroup

	// Helper to run a subsystem goroutine
	run := func(name string, fn func(context.Context) error) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer logger.Info("subsystem stopped", "name", name)
			logger.Info("starting subsystem", "name", name)
			if err := fn(ctx); err != nil && ctx.Err() == nil {
				logger.Error("subsystem failed", "name", name, "error", err)
			}
		}()
	}

	// Start all subsystems
	run("led", ledCtrl.Run)
	run("connectivity", connMon.Run)
	run("system", sysStats.Run)
	run("dashboard", dashSrv.Run)
	run("logs", logAgg.Run)
	run("network", netMgr.Run)
	run("updater", upd.Run)

	// Unblock WiFi radio on every boot (not just first boot)
	if err := exec.CommandContext(ctx, "rfkill", "unblock", "wifi").Run(); err != nil {
		logger.Debug("rfkill unblock wifi", "error", err)
	}
	if err := exec.CommandContext(ctx, "nmcli", "radio", "wifi", "on").Run(); err != nil {
		logger.Debug("nmcli radio wifi on", "error", err)
	}

	// Radio subsystems only run if callsign is configured
	if configured {
		run("direwolf", dwRunner.Run)
		run("gps", gpsReporter.Run)
		run("aprs", aprsListener.Run)
		run("lora", loraReader.Run)
		run("uploader", ul.Run)
	} else {
		logger.Warn("radio subsystems skipped — set callsign via dashboard at :8080 then reboot")
	}

	// Optional kiosk display (cage + cog)
	if cfg.Display.Enabled {
		displayURL := cfg.Display.URL
		if displayURL == "" {
			displayURL = fmt.Sprintf("http://localhost:%d", cfg.Dashboard.Port)
		}
		run("display", func(ctx context.Context) error {
			logger.Info("starting kiosk display", "url", displayURL)

			// cage needs a runtime dir for the Wayland socket
			runtimeDir := "/tmp/umbgs-display"
			if err := os.MkdirAll(runtimeDir, 0700); err != nil {
				return fmt.Errorf("create runtime dir: %w", err)
			}

			displayLogger := logger.With("service", "display")
			for {
				kioskURL := displayURL
				if !strings.Contains(kioskURL, "?") {
					kioskURL += "?kiosk=1"
				} else {
					kioskURL += "&kiosk=1"
				}
				cmd := exec.Command("cage", "--", "cog", kioskURL)
				cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
				cmd.Stdout = &filteredLogWriter{logger: displayLogger}
				cmd.Stderr = &filteredLogWriter{logger: displayLogger}
				cmd.Env = append(os.Environ(),
					"WLR_NO_HARDWARE_CURSORS=1",
					// cage/wlroots always draws a cursor when a pointer-capable
					// input device (touchscreen) is present — there's no flag
					// to disable it. Point at our transparent xcursor theme
					// installed by install.sh at /usr/share/icons/blank.
					"XCURSOR_THEME=blank",
					"XCURSOR_SIZE=1",
					"XDG_RUNTIME_DIR="+runtimeDir,
					"LIBSEAT_BACKEND=builtin",
					"HOME=/tmp",
				)
				if err := cmd.Start(); err != nil {
					if ctx.Err() != nil {
						return ctx.Err()
					}
					logger.Warn("display process failed to start", "error", err)
					select {
					case <-ctx.Done():
						return ctx.Err()
					case <-time.After(5 * time.Second):
					}
					continue
				}
				// Kill process group on context cancel
				go func() {
					<-ctx.Done()
					if cmd.Process != nil {
						syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
					}
				}()
				if err := cmd.Wait(); err != nil {
					if ctx.Err() != nil {
						return ctx.Err()
					}
					logger.Warn("display process exited, restarting", "error", err)
					select {
					case <-ctx.Done():
						return ctx.Err()
					case <-time.After(5 * time.Second):
					}
				}
			}
		})
	}

	// Stats update goroutine - bridges subsystem state to system stats + sd_notify watchdog
	systemdServices := []string{"umbgs", "gpsd", "chrony"}
	run("stats-bridge", func(ctx context.Context) error {
		ticker := time.NewTicker(3 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-ticker.C:
				sdNotify("WATCHDOG=1")
				// Check systemd service statuses
				svcStatus := make(map[string]string, len(systemdServices)+1)
				for _, svc := range systemdServices {
					out, err := exec.CommandContext(ctx, "systemctl", "is-active", svc).Output()
					if err != nil {
						svcStatus[svc] = "inactive"
					} else {
						svcStatus[svc] = strings.TrimSpace(string(out))
					}
				}
				// Direwolf is managed as a subprocess, not systemd
				svcStatus["direwolf"] = dwRunner.Status()
				sysStats.SetExternal(func(st *types.SystemStats) {
					st.Online = connMon.Online()
					st.BufferDepth = buf.Depth()
					st.FailedCount = buf.FailedCount()
					st.LEDState = ledCtrl.GetState()
					st.Network = connMon.Status()
					st.Services = svcStatus
					if pos := gpsReporter.Position(); pos != nil {
						st.GPSFix = true
						st.GPSLat = pos.Lat
						st.GPSLon = pos.Lon
						st.GPSAlt = pos.Alt
					} else {
						st.GPSFix = false
					}
				})
				// Broadcast stats to dashboard clients
				hub.BroadcastStats(sysStats.Get())
			}
		}
	})

	// Event forwarding goroutine
	run("event-forward", func(ctx context.Context) error {
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case evt := <-evtChan:
				hub.BroadcastPacketEvent(evt)
			}
		}
	})

	// LED state management
	run("led-state", func(ctx context.Context) error {
		// Wait for initial connectivity check
		time.Sleep(5 * time.Second)
		ledCtrl.SetState(led.StateOnline)
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-ticker.C:
				if connMon.Online() {
					ledCtrl.SetState(led.StateOnline)
				} else {
					ledCtrl.SetState(led.StateOffline)
				}
			}
		}
	})

	ledCtrl.SetState(led.StateBooting)
	logger.Info("all subsystems started",
		"callsign", cfg.UploaderCallsign(),
		"dashboard", fmt.Sprintf(":%d", cfg.Dashboard.Port),
	)

	// Wait for shutdown signal
	sig := <-sigCh
	logger.Info("received signal, shutting down", "signal", sig)
	cancel()

	// Give subsystems time to clean up
	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		logger.Info("clean shutdown complete")
	case <-time.After(20 * time.Second):
		logger.Warn("shutdown timed out, exiting")
	}

	if lf != nil {
		lf.Close()
	}
}

// openLogFile opens the log file for appending, rotating if it exceeds maxLogSize.
func openLogFile(path string) (*os.File, error) {
	info, err := os.Stat(path)
	if err == nil && info.Size() > int64(maxLogSize) {
		// Rotate: rename current to .1, discard older
		os.Remove(path + ".1")
		os.Rename(path, path+".1")
	}
	return os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
}

// sdNotify sends a message to the systemd notify socket (if available).
func sdNotify(state string) {
	sock := os.Getenv("NOTIFY_SOCKET")
	if sock == "" {
		return
	}
	conn, err := net.Dial("unixgram", sock)
	if err != nil {
		return
	}
	defer conn.Close()
	conn.Write([]byte(state))
}

// writeDebugSnapshot copies recent logs to the FAT32 boot partition for easy retrieval.
func writeDebugSnapshot(logger *slog.Logger) error {
	ts := time.Now().UTC().Format("20060102-150405")
	dst := filepath.Join(snapshotDir, fmt.Sprintf("debug-snapshot-%s.log", ts))

	src, err := os.Open(logFile)
	if err != nil {
		return fmt.Errorf("open log: %w", err)
	}
	defer src.Close()

	// Copy last ~1MB of logs
	info, _ := src.Stat()
	if info.Size() > 1024*1024 {
		src.Seek(-1024*1024, io.SeekEnd)
	}

	out, err := os.Create(dst)
	if err != nil {
		return fmt.Errorf("create snapshot: %w", err)
	}
	defer out.Close()

	if _, err := io.Copy(out, src); err != nil {
		return fmt.Errorf("copy: %w", err)
	}

	logger.Info("debug snapshot written", "path", dst)
	return nil
}

// filteredLogWriter routes subprocess output through slog, suppressing known
// harmless GTK/GLib warnings from cage/cog.
type filteredLogWriter struct {
	logger *slog.Logger
}

// Known noise from cog/cage/GTK that can be safely suppressed.
var displayNoisePatterns = []string{
	"g_application_activate",
	"g_source_destroy",
	"handlers connected to the 'activate' signal",
	"eglQueryDeviceStringEXT",
	"EGL_BAD_PARAMETER",
	"Could not determine the accessibility bus",
}

func (w *filteredLogWriter) Write(p []byte) (int, error) {
	line := strings.TrimSpace(string(p))
	if line == "" {
		return len(p), nil
	}
	for _, pattern := range displayNoisePatterns {
		if strings.Contains(line, pattern) {
			w.logger.Debug(line)
			return len(p), nil
		}
	}
	w.logger.Info(line)
	return len(p), nil
}

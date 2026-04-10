package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
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

	// Generate direwolf.conf (only if configured)
	if configured && cfg.APRS.Enabled {
		if err := direwolf.GenerateConfig(cfgMgr, logger); err != nil {
			logger.Warn("failed to generate direwolf.conf", "error", err)
		}
	}

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

	// Radio subsystems only run if callsign is configured
	if configured {
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

			for {
				cmd := exec.CommandContext(ctx, "cage", "--", "cog", displayURL)
				cmd.Stdout = os.Stdout
				cmd.Stderr = os.Stderr
				cmd.Env = append(os.Environ(),
					"WLR_LIBINPUT_NO_DEVICES=1",
					"XDG_RUNTIME_DIR="+runtimeDir,
					"LIBSEAT_BACKEND=builtin",
					"HOME=/tmp",
				)
				if err := cmd.Run(); err != nil {
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

	// Stats update goroutine - bridges subsystem state to system stats
	run("stats-bridge", func(ctx context.Context) error {
		ticker := time.NewTicker(3 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-ticker.C:
				sysStats.SetExternal(func(st *types.SystemStats) {
					st.Online = connMon.Online()
					st.BufferDepth = buf.Depth()
					st.FailedCount = buf.FailedCount()
					st.LEDState = ledCtrl.GetState()
					st.Network = connMon.Status()
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
	case <-time.After(10 * time.Second):
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

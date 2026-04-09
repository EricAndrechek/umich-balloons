// Package system collects system metrics (CPU, RAM, temp, uptime).
package system

import (
	"context"
	"log/slog"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

// Stats collects and caches system metrics.
type Stats struct {
	logger  *slog.Logger
	version string
	mu      sync.RWMutex
	stats   types.SystemStats
}

// NewStats creates a system stats collector.
func NewStats(version string, logger *slog.Logger) *Stats {
	return &Stats{
		version: version,
		logger:  logger.With("service", "system"),
	}
}

// Get returns the latest system stats snapshot.
func (s *Stats) Get() types.SystemStats {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.stats
}

// Run periodically collects system stats.
func (s *Stats) Run(ctx context.Context) error {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	s.collect()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			s.collect()
		}
	}
}

func (s *Stats) collect() {
	var st types.SystemStats
	st.Version = s.version
	st.Timestamp = time.Now().UTC()
	// CPU usage from /proc/stat (simplified - just load average)
	st.CPUPercent = cpuUsage()
	// RAM from /proc/meminfo
	st.RAMTotalMB, st.RAMUsedMB, st.RAMPercent = memUsage()
	// CPU temperature
	st.CPUTempC = cpuTemp()
	// Uptime
	st.Uptime = uptime()
	// Preserve fields that are set externally
	s.mu.Lock()
	st.Online = s.stats.Online
	st.BufferDepth = s.stats.BufferDepth
	st.FailedCount = s.stats.FailedCount
	st.GPSFix = s.stats.GPSFix
	st.GPSLat = s.stats.GPSLat
	st.GPSLon = s.stats.GPSLon
	st.GPSAlt = s.stats.GPSAlt
	st.Services = s.stats.Services
	st.Network = s.stats.Network
	st.LEDState = s.stats.LEDState
	s.stats = st
	s.mu.Unlock()
}

// SetExternal updates fields from other subsystems.
func (s *Stats) SetExternal(fn func(st *types.SystemStats)) {
	s.mu.Lock()
	fn(&s.stats)
	s.mu.Unlock()
}

func cpuUsage() float64 {
	if runtime.GOOS != "linux" {
		return 0
	}
	data, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return 0
	}
	parts := strings.Fields(string(data))
	if len(parts) < 1 {
		return 0
	}
	load, _ := strconv.ParseFloat(parts[0], 64)
	ncpu := float64(runtime.NumCPU())
	pct := (load / ncpu) * 100
	if pct > 100 {
		pct = 100
	}
	return pct
}

func memUsage() (totalMB, usedMB, pct float64) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return
	}
	var total, available uint64
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		val, _ := strconv.ParseUint(fields[1], 10, 64)
		switch fields[0] {
		case "MemTotal:":
			total = val
		case "MemAvailable:":
			available = val
		}
	}
	if total == 0 {
		return
	}
	totalMB = float64(total) / 1024
	usedMB = float64(total-available) / 1024
	pct = (usedMB / totalMB) * 100
	return
}

func cpuTemp() float64 {
	data, err := os.ReadFile("/sys/class/thermal/thermal_zone0/temp")
	if err != nil {
		return 0
	}
	millideg, _ := strconv.ParseFloat(strings.TrimSpace(string(data)), 64)
	return millideg / 1000.0
}

func uptime() int64 {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0
	}
	parts := strings.Fields(string(data))
	if len(parts) < 1 {
		return 0
	}
	secs, _ := strconv.ParseFloat(parts[0], 64)
	return int64(secs)
}

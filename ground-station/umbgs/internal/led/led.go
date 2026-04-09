// Package led controls the onboard LED for status indication.
package led

import (
	"context"
	"log/slog"
	"os"
	"sync"
	"time"
)

// LED trigger paths on Raspberry Pi.
const (
	ledPath    = "/sys/class/leds/ACT/brightness" // Pi activity LED
	ledTrigger = "/sys/class/leds/ACT/trigger"
)

// State represents the LED display mode.
type State string

const (
	StateBooting State = "booting" // Slow blink
	StateOnline  State = "online"  // Solid on
	StateOffline State = "offline" // Fast blink
	StateUpload  State = "upload"  // Quick flash
	StateError   State = "error"   // Double blink
)

// Controller manages the status LED.
type Controller struct {
	logger *slog.Logger
	mu     sync.Mutex
	state  State
}

// NewController creates an LED controller.
func NewController(logger *slog.Logger) *Controller {
	return &Controller{
		logger: logger.With("service", "led"),
		state:  StateBooting,
	}
}

// SetState changes the LED pattern.
func (c *Controller) SetState(s State) {
	c.mu.Lock()
	c.state = s
	c.mu.Unlock()
}

// GetState returns the current state name.
func (c *Controller) GetState() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return string(c.state)
}

// Run drives the LED pattern until ctx is cancelled.
func (c *Controller) Run(ctx context.Context) error {
	// Take manual control of the LED
	if err := os.WriteFile(ledTrigger, []byte("none"), 0644); err != nil {
		c.logger.Warn("cannot control LED", "error", err)
		<-ctx.Done()
		return ctx.Err()
	}
	c.logger.Info("LED controller started")
	for {
		c.mu.Lock()
		state := c.state
		c.mu.Unlock()
		switch state {
		case StateBooting:
			c.blink(ctx, 500*time.Millisecond)
		case StateOnline:
			c.set(true)
			c.sleep(ctx, time.Second)
		case StateOffline:
			c.blink(ctx, 150*time.Millisecond)
		case StateUpload:
			c.set(true)
			c.sleep(ctx, 50*time.Millisecond)
			c.set(false)
			c.sleep(ctx, 50*time.Millisecond)
		case StateError:
			c.set(true)
			c.sleep(ctx, 100*time.Millisecond)
			c.set(false)
			c.sleep(ctx, 100*time.Millisecond)
			c.set(true)
			c.sleep(ctx, 100*time.Millisecond)
			c.set(false)
			c.sleep(ctx, 500*time.Millisecond)
		default:
			c.sleep(ctx, time.Second)
		}
		if ctx.Err() != nil {
			c.set(false)
			return ctx.Err()
		}
	}
}

func (c *Controller) set(on bool) {
	val := "0"
	if on {
		val = "1"
	}
	os.WriteFile(ledPath, []byte(val), 0644)
}

func (c *Controller) blink(ctx context.Context, d time.Duration) {
	c.set(true)
	c.sleep(ctx, d)
	c.set(false)
	c.sleep(ctx, d)
}

func (c *Controller) sleep(ctx context.Context, d time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}

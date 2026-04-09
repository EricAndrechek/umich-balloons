// Package uploader sends packets to the API using HTTP with MessagePack+gzip.
package uploader

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/quic-go/quic-go/http3"
	"github.com/vmihailenco/msgpack/v5"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/buffer"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/config"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/connectivity"
	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"
)

const (
	maxRetries     = 3
	flushBatchSize = 50 // max buffered packets to flush at once
)

// Uploader reads packets from a channel and uploads them to the API.
// When offline, packets are buffered in SQLite. On reconnect, buffered
// packets are flushed automatically.
type Uploader struct {
	cfg    *config.Manager
	buf    *buffer.Store
	conn   *connectivity.Monitor
	in     <-chan types.Packet
	events chan<- types.PacketEvent
	logger *slog.Logger
	h3     *http.Client
	h2     *http.Client
}

// New creates a new uploader.
func New(
	cfg *config.Manager,
	buf *buffer.Store,
	conn *connectivity.Monitor,
	in <-chan types.Packet,
	events chan<- types.PacketEvent,
	logger *slog.Logger,
) *Uploader {
	h3rt := &http3.Transport{
		TLSClientConfig: &tls.Config{},
	}
	h3client := &http.Client{
		Transport: h3rt,
		Timeout:   15 * time.Second,
	}
	h2client := &http.Client{
		Timeout: 15 * time.Second,
	}

	return &Uploader{
		cfg:    cfg,
		buf:    buf,
		conn:   conn,
		in:     in,
		events: events,
		logger: logger.With("service", "uploader"),
		h3:     h3client,
		h2:     h2client,
	}
}

// Run processes incoming packets immediately (no batching) and flushes
// any previously buffered packets when connectivity is restored.
func (u *Uploader) Run(ctx context.Context) error {
	// Flush any packets buffered from a previous run
	if u.conn.Online() {
		u.flushBuffer(ctx)
	}

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()

		case pkt := <-u.in:
			u.handlePacket(ctx, pkt)
		}
	}
}

func (u *Uploader) handlePacket(ctx context.Context, pkt types.Packet) {
	if !u.conn.Online() {
		u.logger.Info("offline, buffering packet", "source", pkt.Source)
		if err := u.buf.Enqueue(pkt); err != nil {
			u.logger.Error("failed to buffer packet", "error", err)
		}
		u.emitEvent(pkt, types.StatusBuffered, "")
		return
	}

	err := u.upload(ctx, pkt)
	if err != nil {
		u.logger.Warn("upload failed, buffering", "source", pkt.Source, "error", err)
		if bufErr := u.buf.Enqueue(pkt); bufErr != nil {
			u.logger.Error("failed to buffer packet after upload failure", "error", bufErr)
		}
		u.emitEvent(pkt, types.StatusBuffered, err.Error())
		return
	}

	u.logger.Info("packet uploaded", "source", pkt.Source, "endpoint", pkt.Endpoint)
	u.emitEvent(pkt, types.StatusUploaded, "")

	// After a successful upload, try flushing any previously buffered packets
	u.flushBuffer(ctx)
}

func (u *Uploader) upload(ctx context.Context, pkt types.Packet) error {
	c := u.cfg.Get()

	payload := map[string]interface{}{
		"sender":    pkt.Sender,
		"timestamp": pkt.Time.UTC().Format(time.RFC3339Nano),
		"raw_data":  pkt.RawData,
	}
	if pkt.Parsed != nil {
		payload["parsed"] = pkt.Parsed
	}

	body, err := encodeMsgpackGzip(payload)
	if err != nil {
		return fmt.Errorf("encode: %w", err)
	}

	url := c.APIUrl + pkt.Endpoint

	var lastErr error
	for attempt := 0; attempt < maxRetries; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(attempt) * time.Second):
			}
		}

		err := u.doRequest(ctx, url, body)
		if err == nil {
			return nil
		}
		lastErr = err
		u.logger.Debug("upload attempt failed", "attempt", attempt+1, "error", err)
	}

	return fmt.Errorf("after %d retries: %w", maxRetries, lastErr)
}

func (u *Uploader) doRequest(ctx context.Context, url string, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/msgpack")
	req.Header.Set("Content-Encoding", "gzip")

	// Try HTTP/3 first, fall back to HTTP/2
	resp, err := u.h3.Do(req)
	if err != nil {
		u.logger.Debug("HTTP/3 failed, trying HTTP/2", "error", err)
		req2, _ := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
		req2.Header.Set("Content-Type", "application/msgpack")
		req2.Header.Set("Content-Encoding", "gzip")
		resp, err = u.h2.Do(req2)
		if err != nil {
			return fmt.Errorf("request: %w", err)
		}
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	return fmt.Errorf("HTTP %d", resp.StatusCode)
}

func (u *Uploader) flushBuffer(ctx context.Context) {
	pkts, err := u.buf.Drain(flushBatchSize)
	if err != nil {
		u.logger.Error("failed to drain buffer", "error", err)
		return
	}
	if len(pkts) == 0 {
		return
	}

	u.logger.Info("flushing buffered packets", "count", len(pkts))
	var uploaded []int64

	for _, bp := range pkts {
		err := u.upload(ctx, bp.Pkt)
		if err != nil {
			u.logger.Warn("flush upload failed, will retry later", "error", err)
			break
		}
		uploaded = append(uploaded, bp.ID)
		u.emitEvent(bp.Pkt, types.StatusUploaded, "")
	}

	if len(uploaded) > 0 {
		if err := u.buf.Remove(uploaded); err != nil {
			u.logger.Error("failed to remove flushed packets", "error", err)
		}
		u.logger.Info("flushed packets", "count", len(uploaded))
	}
}

func (u *Uploader) emitEvent(pkt types.Packet, status types.PacketStatus, errMsg string) {
	evt := types.PacketEvent{
		Packet:    pkt,
		Status:    status,
		Error:     errMsg,
		Timestamp: time.Now().UTC(),
	}
	select {
	case u.events <- evt:
	default:
		// Don't block if dashboard isn't consuming events
	}
}

func encodeMsgpackGzip(v interface{}) ([]byte, error) {
	msgpackData, err := msgpack.Marshal(v)
	if err != nil {
		return nil, err
	}

	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if _, err := gz.Write(msgpackData); err != nil {
		return nil, err
	}
	if err := gz.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

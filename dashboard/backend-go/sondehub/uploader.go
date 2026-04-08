package sondehub

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"sync"
	"time"
)

// Uploader batches telemetry and uploads to SondeHub Amateur API.
type Uploader struct {
	apiURL          string
	softwareName    string
	softwareVersion string
	devMode         bool
	uploadInterval  time.Duration
	maxRetries      int

	queue  []Telemetry
	mu     sync.Mutex
	done   chan struct{}
	wg     sync.WaitGroup
	client *http.Client
}

// NewUploader creates and starts a new SondeHub uploader.
func NewUploader(apiURL, softwareName, softwareVersion string, devMode bool, uploadIntervalSec int) *Uploader {
	u := &Uploader{
		apiURL:          apiURL,
		softwareName:    softwareName,
		softwareVersion: softwareVersion,
		devMode:         devMode,
		uploadInterval:  time.Duration(uploadIntervalSec) * time.Second,
		maxRetries:      5,
		queue:           make([]Telemetry, 0, 64),
		done:            make(chan struct{}),
		client: &http.Client{
			Timeout: 20 * time.Second,
		},
	}
	u.wg.Add(1)
	go u.uploadLoop()
	return u
}

// Add queues a telemetry packet for upload.
func (u *Uploader) Add(t Telemetry) {
	// Stamp common fields
	t.SoftwareName = u.softwareName
	t.SoftwareVersion = u.softwareVersion
	if t.TimeReceived == "" {
		t.TimeReceived = Now()
	}

	u.mu.Lock()
	u.queue = append(u.queue, t)
	u.mu.Unlock()
}

// Close stops the upload loop, flushing remaining packets.
func (u *Uploader) Close() {
	close(u.done)
	u.wg.Wait()
}

func (u *Uploader) uploadLoop() {
	defer u.wg.Done()
	ticker := time.NewTicker(u.uploadInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			u.flush()
		case <-u.done:
			u.flush() // Final flush
			return
		}
	}
}

func (u *Uploader) flush() {
	u.mu.Lock()
	if len(u.queue) == 0 {
		u.mu.Unlock()
		return
	}
	batch := u.queue
	u.queue = make([]Telemetry, 0, 64)
	u.mu.Unlock()

	if err := u.upload(batch); err != nil {
		log.Printf("ERROR: SondeHub upload failed: %v", err)
	}
}

func (u *Uploader) upload(batch []Telemetry) error {
	// Marshal each telemetry item, merging ExtraFields
	jsonItems := make([]json.RawMessage, 0, len(batch))
	for _, t := range batch {
		raw, err := marshalTelemetry(t)
		if err != nil {
			log.Printf("WARN: skipping telemetry marshal error: %v", err)
			continue
		}
		jsonItems = append(jsonItems, raw)
	}
	if len(jsonItems) == 0 {
		return nil
	}

	body, err := json.Marshal(jsonItems)
	if err != nil {
		return fmt.Errorf("marshal batch: %w", err)
	}

	// Gzip compress
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	if _, err := gz.Write(body); err != nil {
		return fmt.Errorf("gzip write: %w", err)
	}
	if err := gz.Close(); err != nil {
		return fmt.Errorf("gzip close: %w", err)
	}

	preSize := len(body)
	postSize := buf.Len()
	log.Printf("INFO: SondeHub upload: %d packets, %d bytes pre-gzip, %d bytes post-gzip (%.1f%% ratio)",
		len(jsonItems), preSize, postSize, float64(postSize)/float64(preSize)*100)

	// Retry loop
	var lastErr error
	for attempt := 0; attempt <= u.maxRetries; attempt++ {
		if attempt > 0 {
			backoff := time.Duration(1<<uint(attempt-1)) * time.Second
			log.Printf("INFO: SondeHub upload retry %d/%d after %v", attempt, u.maxRetries, backoff)
			time.Sleep(backoff)
		}

		req, err := http.NewRequest(http.MethodPut, u.apiURL+"/amateur/telemetry", bytes.NewReader(buf.Bytes()))
		if err != nil {
			return fmt.Errorf("create request: %w", err)
		}
		req.Header.Set("User-Agent", fmt.Sprintf("%s-%s", u.softwareName, u.softwareVersion))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Content-Encoding", "gzip")
		req.Header.Set("Date", time.Now().UTC().Format(http.TimeFormat))

		resp, err := u.client.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("http request: %w", err)
			continue
		}

		respBody, _ := io.ReadAll(resp.Body)
		resp.Body.Close()

		if resp.StatusCode == 200 {
			log.Printf("INFO: SondeHub upload successful (%d packets)", len(jsonItems))
			return nil
		}

		if resp.StatusCode >= 500 {
			lastErr = fmt.Errorf("server error %d: %s", resp.StatusCode, string(respBody))
			continue // Retry on 5xx
		}

		// 2xx (non-200) or 4xx — log but don't retry
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			if u.devMode {
				log.Printf("INFO: SondeHub accepted (dev mode) %d: %s", resp.StatusCode, string(respBody))
			} else {
				log.Printf("WARN: SondeHub partial success %d: %s", resp.StatusCode, string(respBody))
			}
			return nil
		}

		return fmt.Errorf("client error %d: %s", resp.StatusCode, string(respBody))
	}

	return fmt.Errorf("upload failed after %d retries: %w", u.maxRetries, lastErr)
}

// marshalTelemetry marshals a Telemetry struct, merging ExtraFields into the JSON.
func marshalTelemetry(t Telemetry) (json.RawMessage, error) {
	extras := t.ExtraFields
	t.ExtraFields = nil // Prevent recursion

	data, err := json.Marshal(t)
	if err != nil {
		return nil, err
	}

	if len(extras) == 0 {
		return data, nil
	}

	// Merge extra fields into the JSON object
	var merged map[string]interface{}
	if err := json.Unmarshal(data, &merged); err != nil {
		return nil, err
	}
	for k, v := range extras {
		if _, exists := merged[k]; !exists {
			merged[k] = v
		}
	}
	return json.Marshal(merged)
}

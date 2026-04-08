package handlers

import (
	"encoding/json"
	"io"
	"log"
	"net/http"

	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/normalize"
	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/sondehub"
)

// APRSRequest matches the existing APRSMessage schema.
type APRSRequest struct {
	MessageID string `json:"message_id,omitempty"`
	Sender    string `json:"sender,omitempty"`
	RawData   string `json:"raw_data"`
	Timestamp string `json:"timestamp,omitempty"`
}

// APRSHandler handles POST /aprs requests.
func APRSHandler(uploader *sondehub.Uploader) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req APRSRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			// Also handle raw string body (just the APRS packet)
			r.Body.Close()
			log.Printf("WARN: APRS bad JSON, trying raw body")
			http.Error(w, "invalid request", http.StatusBadRequest)
			return
		}

		if req.RawData == "" {
			http.Error(w, "missing raw_data", http.StatusBadRequest)
			return
		}

		sender := req.Sender
		if sender == "" {
			sender = r.RemoteAddr
		}

		telem, err := normalize.ParseAPRS(req.RawData, sender)
		if err != nil {
			log.Printf("WARN: APRS parse error: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		uploader.Add(*telem)
		log.Printf("INFO: APRS packet queued from %s, callsign=%s", sender, telem.PayloadCallsign)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"status": "queued",
		})
	}
}

// APRSRawHandler accepts a raw APRS packet as the request body (non-JSON).
func APRSRawHandler(uploader *sondehub.Uploader) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		body, err := io.ReadAll(io.LimitReader(r.Body, 4096))
		if err != nil || len(body) == 0 {
			http.Error(w, "empty body", http.StatusBadRequest)
			return
		}

		telem, err := normalize.ParseAPRS(string(body), r.RemoteAddr)
		if err != nil {
			log.Printf("WARN: APRS raw parse error: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		uploader.Add(*telem)
		log.Printf("INFO: APRS raw packet queued, callsign=%s", telem.PayloadCallsign)

		w.WriteHeader(http.StatusAccepted)
	}
}

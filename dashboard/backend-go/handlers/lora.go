package handlers

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/normalize"
	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/sondehub"
)

// LoRaRequest matches the existing LoRaMessage schema.
type LoRaRequest struct {
	MessageID string          `json:"message_id,omitempty"`
	Sender    string          `json:"sender,omitempty"`
	RawData   json.RawMessage `json:"raw_data"`
	Timestamp string          `json:"timestamp,omitempty"`
}

// LoRaHandler handles POST /lora requests.
func LoRaHandler(uploader *sondehub.Uploader) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req LoRaRequest
		dec := json.NewDecoder(r.Body)
		dec.UseNumber()
		if err := dec.Decode(&req); err != nil {
			log.Printf("WARN: LoRa bad request: %v", err)
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}

		sender := req.Sender
		if sender == "" {
			sender = r.RemoteAddr
		}

		// Parse raw_data — could be a JSON object or a JSON string containing JSON
		var rawData interface{}
		if err := json.Unmarshal(req.RawData, &rawData); err != nil {
			log.Printf("WARN: LoRa raw_data unmarshal error: %v", err)
			http.Error(w, "invalid raw_data", http.StatusBadRequest)
			return
		}

		// If raw_data was a JSON string, parse the inner JSON
		if s, ok := rawData.(string); ok {
			var inner interface{}
			if err := json.Unmarshal([]byte(s), &inner); err == nil {
				rawData = inner
			}
		}

		telem, err := normalize.ParseLoRaJSON(rawData, sender)
		if err != nil {
			log.Printf("WARN: LoRa parse error: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		uploader.Add(*telem)
		log.Printf("INFO: LoRa packet queued from %s, callsign=%s", sender, telem.PayloadCallsign)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"status": "queued",
		})
	}
}

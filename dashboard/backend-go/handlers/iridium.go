package handlers

import (
	"crypto/rsa"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/config"
	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/normalize"
	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/sondehub"
	"github.com/golang-jwt/jwt/v5"
)

// IridiumRequest matches the Iridium webhook payload from Ground Control (Rock7).
type IridiumRequest struct {
	MOMSN            int     `json:"momsn"`
	IMEI             string  `json:"imei"`
	Data             string  `json:"data"`
	Serial           int     `json:"serial"`
	DeviceType       string  `json:"device_type"`
	IridiumLatitude  float64 `json:"iridium_latitude"`
	IridiumLongitude float64 `json:"iridium_longitude"`
	IridiumCEP       float64 `json:"iridium_cep"`
	TransmitTime     string  `json:"transmit_time"`
	JWT              string  `json:"JWT"`
}

// IridiumHandler handles POST /iridium requests.
func IridiumHandler(uploader *sondehub.Uploader, cfg *config.Config) http.HandlerFunc {
	// Parse the public key once at setup
	pubKey, err := parseRSAPublicKey(cfg.GroundControlPublicKeyPEM)
	if err != nil {
		log.Fatalf("FATAL: failed to parse Ground Control public key: %v", err)
	}

	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req IridiumRequest
		dec := json.NewDecoder(r.Body)
		dec.UseNumber()
		if err := dec.Decode(&req); err != nil {
			log.Printf("WARN: Iridium bad request: %v", err)
			http.Error(w, "invalid JSON", http.StatusBadRequest)
			return
		}

		// Verify JWT (skip in dev mode)
		if cfg.DevMode {
			log.Printf("INFO: Iridium JWT verification skipped (dev mode)")
		} else if err := verifyJWT(req.JWT, pubKey); err != nil {
			log.Printf("WARN: Iridium JWT verification failed: %v", err)
			http.Error(w, "JWT verification failed", http.StatusUnauthorized)
			return
		}

		// Decode hex data field
		dataBytes, err := hex.DecodeString(req.Data)
		if err != nil {
			log.Printf("WARN: Iridium hex decode failed: %v", err)
			http.Error(w, "failed to decode hex data", http.StatusBadRequest)
			return
		}

		// Parse decoded data as JSON
		var payload map[string]interface{}
		if err := json.Unmarshal(dataBytes, &payload); err != nil {
			log.Printf("WARN: Iridium data JSON parse failed: %v", err)
			http.Error(w, "decoded data is not valid JSON", http.StatusBadRequest)
			return
		}

		// Parse transmit time as fallback timestamp
		if req.TransmitTime != "" {
			if _, exists := payload["timestamp"]; !exists {
				if t, err := time.Parse("06-01-02 15:04:05", req.TransmitTime); err == nil {
					payload["timestamp"] = t.UTC().Format(sondehub.TimeFormat)
				}
			}
		}

		// Use IMEI as uploader identifier
		sender := "iridium-" + req.IMEI

		telem, err := normalize.ParseLoRaJSON(payload, sender)
		if err != nil {
			log.Printf("WARN: Iridium parse error: %v", err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		// Override modulation — ParseLoRaJSON defaults to "LoRa"
		mod := "Iridium"
		telem.Modulation = &mod

		// Add Iridium-specific extra fields
		if telem.ExtraFields == nil {
			telem.ExtraFields = make(map[string]interface{})
		}
		telem.ExtraFields["iridium_latitude"] = req.IridiumLatitude
		telem.ExtraFields["iridium_longitude"] = req.IridiumLongitude
		telem.ExtraFields["iridium_cep"] = req.IridiumCEP
		telem.ExtraFields["imei"] = req.IMEI
		telem.ExtraFields["momsn"] = req.MOMSN

		uploader.Add(*telem)
		log.Printf("INFO: Iridium packet queued from IMEI %s, callsign=%s", req.IMEI, telem.PayloadCallsign)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]string{
			"status": "queued",
		})
	}
}

func parseRSAPublicKey(pemStr string) (*rsa.PublicKey, error) {
	block, _ := pem.Decode([]byte(pemStr))
	if block == nil {
		return nil, fmt.Errorf("failed to decode PEM block")
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("failed to parse public key: %w", err)
	}
	rsaPub, ok := pub.(*rsa.PublicKey)
	if !ok {
		return nil, fmt.Errorf("key is not an RSA public key")
	}
	return rsaPub, nil
}

func verifyJWT(tokenStr string, pubKey *rsa.PublicKey) error {
	_, err := jwt.Parse(tokenStr, func(token *jwt.Token) (interface{}, error) {
		if _, ok := token.Method.(*jwt.SigningMethodRSA); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
		}
		return pubKey, nil
	})
	return err
}

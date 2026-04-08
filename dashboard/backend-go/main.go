package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/config"
	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/handlers"
	"github.com/EricAndrechek/umich-balloons/dashboard/backend-go/sondehub"
)

func main() {
	cfg := config.Load()
	log.Printf("Starting umich-balloons relay v%s", cfg.SoftwareVersion)
	log.Printf("SondeHub API: %s, Dev: %v",
		cfg.SondehubAPIURL, cfg.DevMode)

	uploader := sondehub.NewUploader(
		cfg.SondehubAPIURL, cfg.SoftwareName,
		cfg.SoftwareVersion, cfg.DevMode, cfg.UploadInterval,
	)

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handlers.HealthHandler())
	mux.HandleFunc("/aprs", handlers.APRSHandler(uploader))
	mux.HandleFunc("/aprs/raw", handlers.APRSRawHandler(uploader))
	mux.HandleFunc("/lora", handlers.LoRaHandler(uploader))
	mux.HandleFunc("/iridium", handlers.IridiumHandler(uploader, cfg))

	srv := &http.Server{
		Addr:         cfg.ListenAddr,
		Handler:      withLogging(mux),
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)
	go func() {
		log.Printf("Listening on %s", cfg.ListenAddr)
		if err := srv.ListenAndServe(); err != http.ErrServerClosed {
			log.Fatalf("server: %v", err)
		}
	}()

	<-quit
	log.Println("Shutting down...")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	srv.Shutdown(ctx)
	uploader.Close()
	log.Println("Done")
}

func withLogging(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t := time.Now()
		h.ServeHTTP(w, r)
		log.Printf("%s %s %s %v", r.Method, r.URL.Path, r.RemoteAddr, time.Since(t))
	})
}

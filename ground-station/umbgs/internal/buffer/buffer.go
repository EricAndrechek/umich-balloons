// Package buffer provides SQLite-backed packet buffering for offline operation.
package buffer

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"github.com/EricAndrechek/umich-balloons/ground-station/umbgs/internal/types"

	_ "modernc.org/sqlite"
)

// DefaultPath is the production database path on the Pi. Tests can pass
// their own temp path via OpenAt.
const DefaultPath = "/data/buffer.db"

// Store manages an SQLite database for buffering packets when offline
// and permanently storing failed packets.
type Store struct {
	db     *sql.DB
	logger *slog.Logger
}

// Open creates or opens the buffer database at the default production path.
func Open(logger *slog.Logger) (*Store, error) {
	return OpenAt(DefaultPath, logger)
}

// OpenAt creates or opens the buffer database at a custom path. Used by tests.
func OpenAt(path string, logger *slog.Logger) (*Store, error) {
	db, err := sql.Open("sqlite", path+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}

	if err := migrate(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("migrate: %w", err)
	}

	return &Store{db: db, logger: logger.With("service", "buffer")}, nil
}

func migrate(db *sql.DB) error {
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS buffered (
			id        INTEGER PRIMARY KEY AUTOINCREMENT,
			source    TEXT NOT NULL,
			endpoint  TEXT NOT NULL,
			raw_data  TEXT NOT NULL,
			parsed    TEXT,
			sender    TEXT NOT NULL,
			timestamp TEXT NOT NULL
		);
		CREATE TABLE IF NOT EXISTS failed (
			id         INTEGER PRIMARY KEY AUTOINCREMENT,
			source     TEXT NOT NULL,
			endpoint   TEXT NOT NULL,
			raw_data   TEXT NOT NULL,
			parsed     TEXT,
			sender     TEXT NOT NULL,
			timestamp  TEXT NOT NULL,
			error      TEXT NOT NULL,
			failed_at  TEXT NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_buffered_ts ON buffered(timestamp);
		CREATE INDEX IF NOT EXISTS idx_failed_ts ON failed(failed_at);
	`)
	return err
}

// Enqueue stores a packet in the buffer for later upload.
func (s *Store) Enqueue(pkt types.Packet) error {
	parsed, _ := json.Marshal(pkt.Parsed)
	_, err := s.db.Exec(
		`INSERT INTO buffered (source, endpoint, raw_data, parsed, sender, timestamp) VALUES (?, ?, ?, ?, ?, ?)`,
		pkt.Source, pkt.Endpoint, pkt.RawData, string(parsed), pkt.Sender, pkt.Time.UTC().Format(time.RFC3339Nano),
	)
	if err != nil {
		return fmt.Errorf("enqueue: %w", err)
	}
	s.logger.Debug("packet buffered", "source", pkt.Source)
	return nil
}

// Depth returns the number of buffered packets.
func (s *Store) Depth() int {
	var n int
	s.db.QueryRow("SELECT COUNT(*) FROM buffered").Scan(&n)
	return n
}

// Drain retrieves up to limit packets from the buffer, oldest first.
func (s *Store) Drain(limit int) ([]BufferedPacket, error) {
	rows, err := s.db.Query(
		"SELECT id, source, endpoint, raw_data, parsed, sender, timestamp FROM buffered ORDER BY id ASC LIMIT ?",
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var pkts []BufferedPacket
	for rows.Next() {
		var bp BufferedPacket
		var parsed, ts string
		if err := rows.Scan(&bp.ID, &bp.Pkt.Source, &bp.Pkt.Endpoint, &bp.Pkt.RawData, &parsed, &bp.Pkt.Sender, &ts); err != nil {
			return nil, err
		}
		if parsed != "" && parsed != "null" {
			json.Unmarshal([]byte(parsed), &bp.Pkt.Parsed)
		}
		bp.Pkt.Time, _ = time.Parse(time.RFC3339Nano, ts)
		pkts = append(pkts, bp)
	}
	return pkts, rows.Err()
}

// Remove deletes successfully uploaded buffered packets by ID.
func (s *Store) Remove(ids []int64) error {
	if len(ids) == 0 {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	stmt, err := tx.Prepare("DELETE FROM buffered WHERE id = ?")
	if err != nil {
		tx.Rollback()
		return err
	}
	defer stmt.Close()
	for _, id := range ids {
		stmt.Exec(id)
	}
	return tx.Commit()
}

// RecordFailure stores a permanently failed packet.
func (s *Store) RecordFailure(pkt types.Packet, errMsg string) error {
	parsed, _ := json.Marshal(pkt.Parsed)
	_, err := s.db.Exec(
		`INSERT INTO failed (source, endpoint, raw_data, parsed, sender, timestamp, error, failed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		pkt.Source, pkt.Endpoint, pkt.RawData, string(parsed), pkt.Sender,
		pkt.Time.UTC().Format(time.RFC3339Nano), errMsg,
		time.Now().UTC().Format(time.RFC3339Nano),
	)
	return err
}

// FailedCount returns the number of permanently failed packets.
func (s *Store) FailedCount() int {
	var n int
	s.db.QueryRow("SELECT COUNT(*) FROM failed").Scan(&n)
	return n
}

// FailedPackets retrieves recent failed packets for the dashboard.
func (s *Store) FailedPackets(ctx context.Context, limit int) ([]FailedPacket, error) {
	rows, err := s.db.QueryContext(ctx,
		"SELECT id, source, raw_data, sender, timestamp, error, failed_at FROM failed ORDER BY id DESC LIMIT ?",
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []FailedPacket
	for rows.Next() {
		var fp FailedPacket
		var ts, failedAt string
		if err := rows.Scan(&fp.ID, &fp.Source, &fp.RawData, &fp.Sender, &ts, &fp.Error, &failedAt); err != nil {
			return nil, err
		}
		fp.Timestamp, _ = time.Parse(time.RFC3339Nano, ts)
		fp.FailedAt, _ = time.Parse(time.RFC3339Nano, failedAt)
		out = append(out, fp)
	}
	return out, rows.Err()
}

// Close closes the database.
func (s *Store) Close() error {
	return s.db.Close()
}

// BufferedPacket pairs a database row ID with its packet for removal after upload.
type BufferedPacket struct {
	ID  int64
	Pkt types.Packet
}

// FailedPacket is a permanently failed packet for dashboard display.
type FailedPacket struct {
	ID        int64
	Source    string
	RawData   string
	Sender    string
	Timestamp time.Time
	Error     string
	FailedAt  time.Time
}

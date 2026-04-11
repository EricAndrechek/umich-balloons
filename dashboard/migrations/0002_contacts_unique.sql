-- Force every contact row to have a non-null modulation so the unique index
-- below treats them correctly (SQLite considers NULLs distinct inside a
-- UNIQUE constraint, which would silently permit duplicate rows for NULL
-- modulations).
UPDATE contacts SET modulation = 'unknown' WHERE modulation IS NULL;

-- Deduplicate any existing duplicate rows so the unique index can be created.
-- Keep the row with the smallest id for each tuple.
DELETE FROM contacts
WHERE id NOT IN (
  SELECT MIN(id)
  FROM contacts
  GROUP BY launch_group_id, balloon_callsign, uploader_callsign, modulation, contact_time
);

-- The cron now relies on this index + INSERT OR IGNORE to dedupe telemetry
-- records across runs instead of the brittle last_processed_datetime scalar,
-- which dropped late-arriving records on slow upload paths (Iridium, etc.)
-- whenever a faster path had already advanced the high-water mark.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_unique
  ON contacts(launch_group_id, balloon_callsign, uploader_callsign, modulation, contact_time);

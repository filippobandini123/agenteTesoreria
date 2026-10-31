-- Migrazione 29/10/2026: console della tesoreria.
SET search_path = tesoreria;
ALTER TABLE tes_run ADD COLUMN IF NOT EXISTS avviato_da TEXT;

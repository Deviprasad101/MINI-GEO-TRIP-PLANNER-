-- Temple QR visit flow (GeoTrip Planner)
-- Secure token format: GTP-USER-8F3K9X2P (8 hex chars)
-- Run against your PostgreSQL DB if tables/columns are missing.

-- Extend users (app also runs lightweight ALTER on startup)
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS qr_token VARCHAR(32);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_qr_token ON users (qr_token);

-- Temples
CREATE TABLE IF NOT EXISTS temples (
    id SERIAL PRIMARY KEY,
    temple_name VARCHAR(120) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    location VARCHAR(200)
);

-- Daily visits per user per temple
CREATE TABLE IF NOT EXISTS temple_visits (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    temple_id INTEGER NOT NULL REFERENCES temples(id) ON DELETE CASCADE,
    visit_date DATE NOT NULL,
    visit_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_user_temple_day UNIQUE (user_id, temple_id, visit_date)
);

CREATE INDEX IF NOT EXISTS ix_temple_visits_user_id ON temple_visits (user_id);
CREATE INDEX IF NOT EXISTS ix_temple_visits_temple_id ON temple_visits (temple_id);
CREATE INDEX IF NOT EXISTS ix_temple_visits_visit_date ON temple_visits (visit_date);

-- Temple list is synced from tirupati_main_data.csv on app startup (category = Temple, ~20 rows).
-- Optional columns (added by app migration):
ALTER TABLE temples ADD COLUMN IF NOT EXISTS csv_name VARCHAR(200);
ALTER TABLE temples ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION;
ALTER TABLE temples ADD COLUMN IF NOT EXISTS lng DOUBLE PRECISION;

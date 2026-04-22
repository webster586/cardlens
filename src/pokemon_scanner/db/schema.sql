CREATE TABLE IF NOT EXISTS collection_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_id TEXT,
    name TEXT NOT NULL,
    set_name TEXT,
    card_number TEXT,
    language TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    last_price REAL,
    price_currency TEXT,
    notes TEXT,
    image_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_entry_id INTEGER,
    source TEXT NOT NULL,
    price REAL,
    currency TEXT,
    captured_at TEXT NOT NULL,
    FOREIGN KEY(collection_entry_id) REFERENCES collection_entries(id)
);

CREATE TABLE IF NOT EXISTS scan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path TEXT,
    selected_candidate_name TEXT,
    selected_candidate_set TEXT,
    selected_candidate_number TEXT,
    selected_candidate_language TEXT,
    confidence REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    set_name TEXT,
    card_number TEXT,
    language TEXT,
    best_price REAL,
    price_currency TEXT,
    image_url TEXT,
    local_image_path TEXT,
    set_logo_url TEXT DEFAULT '',
    set_local_logo_path TEXT,
    fetched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_documents (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE,
    source_id TEXT,
    source_name TEXT,
    language TEXT,
    text TEXT,
    published_at TEXT,
    date_bs TEXT,
    category TEXT,
    province TEXT,
    district TEXT,
    tags JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_training_documents_source_id ON training_documents(source_id);
CREATE INDEX IF NOT EXISTS idx_training_documents_language ON training_documents(language);
CREATE INDEX IF NOT EXISTS idx_training_documents_created_at ON training_documents(created_at);

CREATE TABLE IF NOT EXISTS visited_urls (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_visited_urls_first_seen_at ON visited_urls(first_seen_at);

CREATE TABLE IF NOT EXISTS raw_records (
    url TEXT PRIMARY KEY,
    source_id TEXT,
    source_name TEXT,
    title TEXT,
    summary TEXT,
    content TEXT,
    language TEXT,
    published_at TEXT,
    date_bs TEXT,
    category TEXT,
    fetched_at TEXT,
    raw_meta JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_raw_records_source_id ON raw_records(source_id);
CREATE INDEX IF NOT EXISTS idx_raw_records_created_at ON raw_records(created_at);

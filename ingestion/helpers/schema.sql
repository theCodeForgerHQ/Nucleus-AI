CREATE TABLE IF NOT EXISTS kb_pages (
  page_id     TEXT PRIMARY KEY,
  page_title  TEXT NOT NULL,
  source_url  TEXT NOT NULL,
  is_active   BOOLEAN NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_chunks (
  chunk_hash  TEXT PRIMARY KEY,
  page_id     TEXT NOT NULL,
  raw_chunk   TEXT NOT NULL,
  is_active   BOOLEAN NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL,

  CONSTRAINT kb_chunks_page_id_fkey
    FOREIGN KEY (page_id)
    REFERENCES kb_pages(page_id)
    ON DELETE RESTRICT
);

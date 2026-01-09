CREATE TABLE kb_pages (
  page_id TEXT PRIMARY KEY,
  page_title VARCHAR NOT NULL,
  is_active BOOLEAN NOT NULL,
  source_url TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE kb_chunks (
  chunk_hash TEXT PRIMARY KEY,
  page_id TEXT NOT NULL,
  raw_chunk TEXT NOT NULL,
  is_active BOOLEAN NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT kb_chunks_page_id_fkey
    FOREIGN KEY (page_id)
    REFERENCES kb_pages (page_id)
    ON DELETE RESTRICT
);

CREATE TABLE kb_images (
  image_hash TEXT PRIMARY KEY,
  page_id TEXT NOT NULL,
  image_src TEXT NOT NULL,
  caption TEXT NOT NULL,
  is_active BOOLEAN NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT kb_images_page_id_fkey
    FOREIGN KEY (page_id)
    REFERENCES kb_pages (page_id)
    ON DELETE RESTRICT
);

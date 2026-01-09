-- Table for knowledge base pages
CREATE TABLE kb_pages (
  page_id VARCHAR(8) PRIMARY KEY,
  page_title VARCHAR NOT NULL,
  is_active BOOLEAN NOT NULL,
  source_url TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Table for knowledge base chunks
CREATE TABLE kb_chunks (
  chunk_hash TEXT PRIMARY KEY,
  page_id VARCHAR(8) NOT NULL,
  raw_chunk TEXT NOT NULL,
  is_active BOOLEAN NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  section_path TEXT,
  CONSTRAINT kb_chunks_page_id_fkey
    FOREIGN KEY (page_id)
    REFERENCES public.kb_pages (page_id)
    ON DELETE RESTRICT
);

-- Ensure unique index on chunk_hash
CREATE UNIQUE INDEX kb_chunks_pkey
  ON kb_chunks USING BTREE (chunk_hash);

-- Table for knowledge base images
CREATE TABLE kb_images (
  image_hash TEXT PRIMARY KEY,
  page_id VARCHAR(8) NOT NULL,
  image_src TEXT NOT NULL,
  caption TEXT NOT NULL,
  is_active BOOLEAN NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  CONSTRAINT kb_images_page_id_fkey
    FOREIGN KEY (page_id)
    REFERENCES public.kb_pages (page_id)
    ON DELETE RESTRICT
);

-- Ensure unique index on image_hash
CREATE UNIQUE INDEX kb_images_pkey
  ON kb_images USING BTREE (image_hash);

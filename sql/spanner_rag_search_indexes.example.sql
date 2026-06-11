-- Add full-text search columns and indexes after the bulk JSONL load.

ALTER TABLE RagDocuments ADD COLUMN TextTokens TOKENLIST
  AS (TOKENIZE_FULLTEXT(TextForEmbedding, content_type=>"text/plain")) HIDDEN;

ALTER TABLE RagChunks ADD COLUMN ChunkTokens TOKENLIST
  AS (TOKENIZE_FULLTEXT(TextForEmbedding, content_type=>"text/plain")) HIDDEN;

CREATE SEARCH INDEX RagChunksTextIndex
ON RagChunks(ChunkTokens)
STORING (SourceDataset, PublishedAt, Source, TokenEstimate);

-- Cloud Spanner GoogleSQL load schema for the private commodity RAG corpus.
--
-- Apply this first, load JSONL into RagDocuments and RagChunks, then run
-- sql/spanner_rag_search_indexes.example.sql and
-- sql/spanner_rag_embedding_backfill.example.sql.

CREATE TABLE RagDocuments (
  DocId STRING(64) NOT NULL,
  SourceDataset STRING(128) NOT NULL,
  SourceFile STRING(MAX),
  SourceRow INT64,
  ExternalId STRING(MAX),
  Title STRING(MAX),
  Body STRING(MAX),
  TextForEmbedding STRING(MAX),
  PublishedAt TIMESTAMP,
  Source STRING(MAX),
  Commodities ARRAY<STRING(MAX)>,
  Tags ARRAY<STRING(MAX)>,
  SentimentJson STRING(MAX),
  MetadataJson STRING(MAX),
  CreatedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true)
) PRIMARY KEY (DocId);

CREATE TABLE RagChunks (
  DocId STRING(64) NOT NULL,
  ChunkId STRING(64) NOT NULL,
  SourceDataset STRING(128) NOT NULL,
  ChunkIndex INT64 NOT NULL,
  CharStart INT64,
  ChunkText STRING(MAX),
  TextForEmbedding STRING(MAX),
  TokenEstimate INT64,
  PublishedAt TIMESTAMP,
  Source STRING(MAX),
  Commodities ARRAY<STRING(MAX)>,
  Tags ARRAY<STRING(MAX)>,
  MetadataJson STRING(MAX),
  Embedding ARRAY<FLOAT32>(vector_length=>768),
  CreatedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true)
) PRIMARY KEY (DocId, ChunkId),
  INTERLEAVE IN PARENT RagDocuments ON DELETE CASCADE;

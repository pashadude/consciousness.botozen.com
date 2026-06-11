-- Cloud Spanner GoogleSQL schema for the private commodity RAG corpus.
--
-- Replace PROJECT_ID, LOCATION, and MODEL_ID before running the CREATE MODEL
-- statement in Spanner Studio or with gcloud spanner databases ddl update.
-- The JSONL converter writes records shaped for RagDocuments and RagChunks.

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
  Sentiment JSON,
  Metadata JSON,
  CreatedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  TextTokens TOKENLIST
    AS (TOKENIZE_FULLTEXT(TextForEmbedding, content_type=>"text/plain")) HIDDEN
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
  Metadata JSON,
  Embedding ARRAY<FLOAT32>(vector_length=>768),
  CreatedAt TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  ChunkTokens TOKENLIST
    AS (TOKENIZE_FULLTEXT(TextForEmbedding, content_type=>"text/plain")) HIDDEN
) PRIMARY KEY (DocId, ChunkId),
  INTERLEAVE IN PARENT RagDocuments ON DELETE CASCADE;

CREATE SEARCH INDEX RagChunksTextIndex
ON RagChunks(ChunkTokens)
STORING (SourceDataset, PublishedAt, Source, TokenEstimate);

-- Create this after most chunk embeddings are populated.
CREATE VECTOR INDEX RagChunksEmbeddingIndex
ON RagChunks(Embedding)
STORING (SourceDataset, PublishedAt, Source, TokenEstimate)
WHERE Embedding IS NOT NULL
OPTIONS (distance_type = 'COSINE', tree_depth = 2, num_leaves = 1000);

CREATE MODEL RagEmbeddingModel
INPUT(
  content STRING(MAX),
  task_type STRING(MAX)
)
OUTPUT(
  embeddings STRUCT<
    statistics STRUCT<truncated BOOL, token_count FLOAT64>,
    values ARRAY<FLOAT64>>
)
REMOTE OPTIONS (
  endpoint = '//aiplatform.googleapis.com/projects/PROJECT_ID/locations/LOCATION/publishers/google/models/MODEL_ID'
);

-- Generate document embeddings in batches. For 1M+ chunks, run this as
-- partitioned DML or an explicit batch worker and create/rebuild the vector
-- index after the bulk load.
UPDATE RagChunks c
SET Embedding = ARRAY(
  SELECT CAST(value AS FLOAT32)
  FROM UNNEST((
    SELECT embeddings.values
    FROM ML.PREDICT(
      MODEL RagEmbeddingModel,
      (SELECT c.TextForEmbedding AS content, "RETRIEVAL_DOCUMENT" AS task_type),
      STRUCT(768 AS outputDimensionality)
    )
  )) AS value
)
WHERE Embedding IS NULL;

-- Example embedding model, backfill, and vector index for Cloud Spanner.
--
-- Replace PROJECT_ID, LOCATION, and MODEL_ID before running. Use this after
-- RagChunks has been loaded. For 1M+ chunks, prefer an explicit batch worker
-- or partitioned DML over one giant UPDATE.
-- The vector index requires a Cloud Spanner Enterprise or Enterprise Plus
-- instance.

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

CREATE VECTOR INDEX RagChunksEmbeddingIndex
ON RagChunks(Embedding)
STORING (SourceDataset, PublishedAt, Source, TokenEstimate)
WHERE Embedding IS NOT NULL
OPTIONS (distance_type = 'COSINE', tree_depth = 2, num_leaves = 1000);

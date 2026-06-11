-- Hybrid full-text + vector search over RagChunks using reciprocal rank fusion.
--
-- Parameters:
--   @query_text STRING
--   @query_vector ARRAY<FLOAT32>(vector_length=>768)
--   @limit INT64
--
-- Generate @query_vector with the same Gemini embedding model and
-- RETRIEVAL_QUERY task type used for document embeddings.

WITH vector_candidates AS (
  SELECT rank, candidate.DocId, candidate.ChunkId
  FROM UNNEST(ARRAY(
    SELECT AS STRUCT DocId, ChunkId
    FROM RagChunks
    WHERE Embedding IS NOT NULL
    ORDER BY APPROX_COSINE_DISTANCE(
      @query_vector,
      Embedding,
      OPTIONS => JSON '{"num_leaves_to_search": 50}'
    )
    LIMIT 100
  )) AS candidate WITH OFFSET AS rank
),
text_candidates AS (
  SELECT rank, candidate.DocId, candidate.ChunkId
  FROM UNNEST(ARRAY(
    SELECT AS STRUCT DocId, ChunkId
    FROM RagChunks
    WHERE SEARCH(ChunkTokens, @query_text)
    ORDER BY SCORE(ChunkTokens, @query_text) DESC
    LIMIT 100
  )) AS candidate WITH OFFSET AS rank
),
fused AS (
  SELECT DocId, ChunkId, SUM(1.0 / (60 + rank)) AS rrf_score
  FROM (
    SELECT DocId, ChunkId, rank FROM vector_candidates
    UNION ALL
    SELECT DocId, ChunkId, rank FROM text_candidates
  )
  GROUP BY DocId, ChunkId
)
SELECT
  f.rrf_score,
  c.DocId,
  c.ChunkId,
  c.SourceDataset,
  c.PublishedAt,
  c.Source,
  c.Commodities,
  c.Tags,
  c.ChunkText,
  c.Metadata
FROM fused AS f
JOIN RagChunks AS c
  ON c.DocId = f.DocId
  AND c.ChunkId = f.ChunkId
ORDER BY f.rrf_score DESC
LIMIT @limit;

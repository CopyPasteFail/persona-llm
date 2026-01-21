# GLOSSARY

## Common LLM and retrieval terms

- **ANN (Approximate Nearest Neighbors)**: A fast, approximate search method for finding the most similar vectors to a query embedding.
- **BM25**: A keyword-based ranking algorithm that scores documents based on term frequency and inverse document frequency.
- **Chunk**: A bounded slice of source text used for retrieval, typically a few hundred tokens with metadata like `role` and `topic`.
- **Citation**: A reference to a retrieved chunk used to justify an answer; surfaced in API responses.
- **Context window**: The maximum amount of tokens a model can accept in a single request (prompt + retrieved chunks + response).
- **Datapoint**: One embedding vector plus its metadata, used for vector search indexing.
- **Dataset version**: A versioned set of artifacts in `datasets/<version>/` containing `chunks.jsonl.gz`, `datapoints.jsonl`, and `manifest.json`.
- **Embedding(s)**: Turning words into a vector. A numeric vector representation of text used for semantic similarity search.
- **Embedding dimensions**: The length of an embedding vector (for example 768 or 3072), which must match the index configuration.
- **Embedding model**: The model used to produce embeddings (configured via `DATAPOINTS_MODEL`).
- **Grounded / grounding**: Constraining answers to the retrieved chunks and avoiding unsupported claims.
- **Hybrid retrieval**: Combining vector similarity (semantic) with BM25 (lexical) signals.
- **Index**: A search data structure storing embeddings for ANN queries.
- **Index endpoint / deployed index**: Vertex AI Matching Engine resources that serve ANN queries for a given index.
- **Matching Engine**: Vertex AI’s managed vector search service (ANN at scale).
- **Persona**: The human profile the system answers as, grounded in the provided dataset.
- **Prompt**: The structured input to the LLM, including instructions and retrieved context.
- **RAG (Retrieval-Augmented Generation)**: An approach that retrieves relevant context and conditions the model on it before generating an answer.
- **Reranking / score blending**: Adjusting and combining retrieval scores (vector + BM25 + boosts) before selecting top results.
- **Role/topic tags**: Metadata applied to chunks (for example `role:infra`, `topic:kubernetes`) used for filtering and boosting.
- **Token**: The model’s unit of text processing; both input and output are measured in tokens.
- **Top-K**: The number of highest-scoring results kept after retrieval.
- **Vector store**: The storage and query layer for embeddings (local in-memory search or Matching Engine).
- **Vector similarity**: The similarity measure between vectors (cosine similarity or dot product with normalized vectors).

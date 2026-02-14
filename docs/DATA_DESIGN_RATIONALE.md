# Data Design Decisions for CV Persona LLM Retrieval Pipeline

## Table of Contents
1. [One or More CV Files per Profile](#1-one-or-more-cv-files-per-profile)
2. [Optional Topic Tags (not profile synonyms)](#2-optional-topic-tags-not-profile-synonyms)
3. [Keep a JSONL “Chunks Sidecar” (Chosen)](#3-keep-a-jsonl-chunks-sidecar-chosen)
4. [ANN via Vertex AI Vector Search](#4-ann-via-vertex-ai-vector-search)
5. [Add BM25 (Keyword Signal)](#5-add-bm25-keyword-signal)
6. [Build BM25 Index at Startup (for now)](#6-build-bm25-index-at-startup-for-now)
7. [Hybrid Retrieval and Rerank (Wide to Narrow)](#7-hybrid-retrieval-and-rerank-wide-to-narrow)
8. [Runtime Classification by Profile](#8-runtime-classification-by-profile)
9. [No Elasticsearch/OpenSearch (for now)](#9-no-elasticsearchopensearch-for-now)
10. [Strict First-Person, Grounded Answers](#10-strict-first-person-grounded-answers)
11. [Structured Metadata + Tags (Denormalization for Retrieval)](#11-structured-metadata--tags-denormalization-for-retrieval)
12. [Embedding Batch Size](#12-embedding-batch-size)
13. [Chunk Identity vs Order (chunk_id and position)](#13-chunk-identity-vs-order-chunk_id-and-position)
14. [Overlap for Retrieval Continuity](#14-overlap-for-retrieval-continuity)
15. [Vertex Matching Engine Update Mode (Batch Update)](#15-vertex-matching-engine-update-mode-batch-update)
16. [Versioned Dataset Folder + Pointer](#16-versioned-dataset-folder--pointer)
17. [Pre-normalized Embeddings](#17-pre-normalized-embeddings)

## 1. One or More CV Files per Profile

**What:** Keep CV content separated into one or more files per profile. Tag all chunks from each file with a single profile tag (for example, `profile:infra`, `profile:product`).  
**Alternatives considered:**
- Merge all profiles into one file.
- Keep only fine-grained title tags (`profile:devops`, `profile:sre`, `profile:platform`, `profile:pm`, `profile:tpm`, `profile:po`) without profile-level grouping.

**Pros:**
- Scales as new profile files are added without changing the ingestion model.
- Easy to steer retrieval by profile without brittle job-title semantics.
- No risk of cross-contamination unless a query is mixed.

**Cons:**
- Less granularity than per-title profiles inside a profile bucket.

**Decision:** Use one profile tag per file/chunk, with the allowed profile set defined by configuration/dataset. Keep profile tags coarse, and use topic tags for finer precision.

---

## 2. Optional Topic Tags (not profile synonyms)

**What:** Add topic tags like `topic:kubernetes`, `topic:roadmap` for precision. Do not duplicate profile synonyms.  
**Alternatives considered:** Encode many profile synonyms as tags.

**Pros:**  
- Topics sharpen retrieval without overfitting to job titles.  
- Less tagging churn if profile taxonomy changes.  

**Cons:** Requires discipline in tagging.

**Decision:** Use topics for precision; keep profiles minimal.

---

## 3. Keep a JSONL “Chunks Sidecar” (Chosen)

**What:** Always keep the gzipped JSONL of chunked text + metadata in GCS alongside the vector index.  
**Alternative (not chosen):** Discard chunks after embedding, keep only vectors.  
**Why people consider that:** Simpler pipeline, smaller footprint.  
**Downsides:** No human-readable source of truth, can’t re-embed if model changes, can’t enrich metadata, debugging opaque.

**Pros:**  
- Auditable, inspectable record of the text.  
- Easy to regenerate vectors with a new model.  
- Enables auxiliary indexes (BM25, keyword filters).  
- Minimal extra storage vs. vector costs.  

**Decision:** Keep both chunks and vectors. Balances transparency, reproducibility, and low ops with almost no overhead.

---

## 4. ANN via Vertex AI Vector Search

**What:** Support two vector backends: local in-process cosine search (default) and Vertex AI Matching Engine (optional).  
**Alternative:** Matching Engine only.  

**Pros:**  
- Local backend keeps ops and serving cost minimal.
- Matching Engine scales to larger corpora and higher query throughput.

**Cons:** Matching Engine requires index provisioning and update steps.  

**Decision:** Keep local as default and allow Matching Engine when needed.

---

## 5. Add BM25 (Keyword Signal)

**What:** Layer a lightweight BM25 scorer on top of ANN results so queries get both lexical and semantic signals.  
**Alternatives considered:** Stay ANN-only; or outsource keyword search to Elasticsearch/OpenSearch.

**Pros:**  
- Catches acronyms, IDs, and rare terms vectors may miss.  
- Zero extra infra when kept in-process.  
- Example: query “experience with KEDA” → BM25 ensures chunks literally mentioning *KEDA* are promoted, even if embeddings underweight it.  

**Cons:** Slightly more code to maintain scoring/weighting logic.  

**Decision:** Keep BM25 as a first-class retrieval signal alongside ANN (build/refresh strategy covered in Decision 8).

---

## 6. Build BM25 Index at Startup (for now)

**What:** Recompute the inverted index during container startup right after loading the chunk file.  
**Alternative:** Precompute BM25 artifacts during ingestion and load them from GCS at runtime.

**Pros:**  
- Always consistent with whatever chunk revision was just pulled.  
- Millisecond build time at this corpus size.  
- Eliminates artifact/version management or cache invalidation logic.  

**Cons:** Startup time will grow with corpus size; cold starts could lengthen if documents explode.  

**Decision:** Add BM25 at startup for now; revisit precomputing only if corpus growth makes boot time material.

---

## 7. Hybrid Retrieval and Rerank (Wide to Narrow)

**What:** Pull `TOP_K` ANN candidates (default 4), weight with BM25 + metadata boosts, then keep at most 8 chunks for prompt context.  
**Alternative:** Fetch exactly 8 from ANN only.

**Pros:**  
- Candidate depth is configurable via `TOP_K`.
- Reranking improves relevance.  
- Hybrid = semantics (ANN) + exact matches (BM25).  
- Example: query “incident response metrics” → ANN finds SRE-related context, BM25 ensures chunks with exact phrase are not missed.  
- Final cap of 8 keeps prompt size bounded.  

**Cons:** Slightly more CPU per query.  

**Decision:** Wide-then-rerank balances semantic breadth with keyword precision. Negligible cost at current scale.

---

## 8. Runtime Classification by Profile

**What:** On each query, classify toward `infra` or `product` using keyword hints. Leave unclassified if mixed.  
**Alternatives:** Let LLM decide; or require user to pick a profile.

**Pros:**  
- Cheap, low-latency.  
- Keeps persona consistent without user having to choose.  
- Mixed queries (e.g. “How do you prioritize infra work?”) can surface both product-style and infra chunks.  
- Prevents wasting LLM tokens on irrelevant profile-specific content.  

**Cons:** Small logic layer to maintain.  

**Decision:** Add a local classifier; bias retrieval softly. Hard filters only if explicitly requested.

---

## 9. No Elasticsearch/OpenSearch (for now)

**What:** Keep search in-process + vector service.  
**Alternative:** Managed search cluster.  

**Pros:**  
- No extra infra or cost.  
- Simple deploys.  

**Cons:** Lacks advanced search features (facets, fuzzy match).  

**Decision:** Skip until corpus is large or features demand it.

---

## 10. Strict First-Person, Grounded Answers

**What:** Normalize queries to first person; ground answers in retrieved chunks with citations and usage.  
**Alternative:** Free-form answers without grounding.  

**Pros:**  
- Consistent persona voice (“I, my, me”).  
- Traceable, debuggable.  

**Cons:** Requires stricter prompt design.  

**Decision:** Stick with grounded, first-person answers. Matches product goal of a reliable persona demo.

---

## 11. Structured Metadata + Tags (Denormalization for Retrieval)

**What:** Keep structured fields in the chunks JSONL (e.g., `profile: "infra" | "product"`, `topics: [...]`) **and** also generate flattened `tags` (e.g., `["profile:infra","topic:kubernetes"]`). Push **only `tags`** into the vector DB metadata for fast filtering; keep the full structured fields in the sidecar JSONL for validation, analytics, and provenance.

**Alternatives considered:**
- **Tags only** (no structured fields): keep just `["profile:infra","topic:kubernetes"]`.
- **Structured only** (no tags): keep `profile`/`topics` but don’t denormalize to `tags`.

**Pros:**
- **At retrieval**: vector DBs (like Vertex AI Matching Engine) filter fastest on flat metadata. `tags` make ANN filtering simple and efficient.
- **Data quality**: structured fields give schema guarantees (e.g., `profile` must be in a configured allowed-profile set), avoiding typos like `profile:infraa`.
- **Analytics & ops**: structured fields are easier to aggregate (“how many infra chunks?”, “topic coverage?”) and evolve (add new fields) without regexing strings.
- **Clarity**: humans can read `profile`/`topics` at a glance; `tags` are a runtime convenience.

**Cons:**
- **Duplication**: `tags` repeat information that’s already in `profile`/`topics`.
- **Slightly more ingestion logic**: must auto-generate tags from structured fields.

**Decision:** **Keep both.**  
- Generate `tags` automatically at ingestion from `profile` + `topics` (deterministic, no manual maintenance).  
- **Push only `tags`** (plus `chunk_id`) to the vector index for fast ANN filtering.  
- Keep **structured fields** in the JSONL sidecar for validation, analytics, and future evolution.

**Example (ingested chunk):**
```json
{
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-001",
  "profile": "infra",
  "topics": ["kubernetes","terraform"],
  "tags": ["profile:infra","topic:kubernetes","topic:terraform"],
  "text": "Ran EKS with Terraform and ArgoCD."
}
```

---

## 12. Embedding Batch Size

**What:** Number of fragments sent per Vertex AI embeddings request (`DATAPOINTS_BATCH_SIZE`, default 16).

**Alternatives considered:** Larger batches (e.g., 50–250) vs. very small batches (e.g., 1–4).

**Pros:**
- Larger: higher throughput, fewer API calls, better server utilization.
- Smaller: lower per-request latency, simpler retries, lower memory footprint.

**Cons:**
- Larger: higher risk of timeouts/failures, bigger payloads, more memory per call.
- Smaller: lower throughput, more API calls, higher chance of hitting rate limits.

**Decision:** Use a moderate default (16) to cut round trips without risking payload limits. Increase for bulk backfills on stable networks; decrease if reliability/latency is critical or if service/model limits are tight.

**Related guardrail:** `DATAPOINTS_DIMENSIONS` defaults to the selected model’s native dimensionality (3,072 for `gemini-embedding-001`, 768 for the `text-embedding-00x` family) so the embeddings always match the Matching Engine index configuration. Only change it when you also plan to recreate the index with that different dimensionality.

**Query usage:**
- ANN call with metadata filter: `tags CONTAINS "profile:infra"`.
- Rerank boost: `tags CONTAINS "topic:kubernetes"`.
- Analytics (outside ANN): group by `profile`, count by `topics`.

---

## 13. Chunk Identity vs Order (chunk_id and position)

**What:** Each chunk carries both a `chunk_id` (unique identifier) and a `position` (order within a document). Today both are generated serially, but they serve different purposes.

**Alternatives considered:**
- **Serial IDs only:** let `chunk_id` imply order; drop `position`.
- **Order only:** drop `chunk_id`, rely on `position` as the ID.
- **Stable hash IDs:** generate `chunk_id` from a hash of the chunk text; keep `position` as order.

**Pros of keeping both:**
- `chunk_id` = identity. Required as a unique key in the vector DB, for citations, and for debugging.
- `position` = order. Lets you reconstruct adjacency and display chunks in sequence, even if ID schemes change later.
- Future-proof: if `chunk_id` changes to a hash or to a per-section scheme, `position` still preserves ordering.

**Cons:**
- Slight duplication when both are serial today: neighbors can be inferred from either.

**Real-world incremental update cases:**
- **Wikis/handbooks:** new paragraph inserted mid-page; hash IDs allow reusing old IDs, `position` preserves order.
- **Release notes:** append a new section weekly; only new IDs are added.
- **Transcripts:** stream chunks as they arrive; existing IDs untouched.

**Decision:** Keep both fields.  
- For this CV app, re-ingestion of the whole doc is simplest; serial `chunk_id` + serial `position` is fine.  
- At larger scale, migrate `chunk_id` to a **content hash** for stability across ingestions, while keeping `position` as the explicit order.  
- If incremental inserts are needed without renumbering, emit `position` with **gaps** (e.g., 10, 20, 30). This lets you slip new chunks in between two existing ones (insert at 15) without reassigning every downstream position.  
- Another option: add a separate `rank` field (float/decimal) to allow flexible ordering while keeping `position` as a simple serial.

---

## 14. Overlap for Retrieval Continuity

**What:** Allow a small (~10%) overlap by sentence between consecutive chunks, but only inside the same profile/employer block.  

**Alternatives considered:**
- No overlap at all, strictly adjacent non-overlapping chunks.
- Larger overlaps (20–30%) to guarantee full context coverage.

**Pros:**
- Preserves continuity when an answer spans the boundary of two chunks.
- Prevents context loss without significantly inflating storage or token usage.
- Keeps overlaps lightweight (10% max) so the dataset does not grow excessively.

**Cons:**
- Slight duplication across chunks increases file size marginally.
- Requires careful implementation to ensure overlaps stay inside profile/employer blocks only.

**Decision:** Add ~10% overlap by sentence within the same profile/employer block. Never overlap across profiles, employers, or across sections (e.g., Experience → Education). This improves retrieval quality by ensuring adjacent chunks share enough context for coherent answers.

---

## 15. Vertex Matching Engine Update Mode (Batch Update)

**What:** Keep the Matching Engine index in the default `BATCH_UPDATE` mode and refresh data by publishing a new datapoints file to Cloud Storage, then running a batch rebuild (`gcloud ai indexes update`).

**Alternative:** Create the index with `STREAM_UPDATE` enabled and push incremental changes with `upsert_datapoints`.

**Pros:**
- Lower steady-state cost: no streaming ingestion charges or background update jobs when the corpus is idle.
- Operationally simple: each refresh is just “export datapoints, upload to GCS, kick off update” with no retry loops or live mutation code paths.
- Matches persona cadence: CV edits happen occasionally, so a longer rebuild window is acceptable.

**Cons:**
- Updates take longer; you must wait for the batch job to finish before the new data is live.
- No real-time inserts/deletes. If data starts changing frequently, the pipeline must shift to streaming.

**Decision:** Use `BATCH_UPDATE` for now. The CV persona changes rarely, and keeping the index in batch mode reduces spend and complexity. We’ll revisit streaming only if we need sub-minute freshness.

---

## 16. Versioned Dataset Folder + Pointer

**What:** Store coupled artifacts in `datasets/<version>/` (`datapoints.jsonl`, `chunks.jsonl.gz`, `manifest.json`) and atomically switch versions by updating `datasets/current.json`.  
**Alternatives considered:** Use per-deploy env vars or hardcode file names in the service.

**Pros:**
- Atomic switch with a tiny pointer write, no partial data loads.
- Easy rollback (republish the pointer to a prior version).
- Single bucket, predictable paths, and explicit manifest for validation.

**Cons:**  
- Requires a manifest discipline and a reload call after pointer updates.

**Decision:** Use a versioned folder with a pointer file. It keeps deployments stable while allowing safe, explicit data changes.

---

## 17. Pre-normalized Embeddings

**What:** Normalize embeddings at ingest time and require unit-length vectors in `datapoints.jsonl`.  
**Alternatives considered:** Normalize at query time only or normalize both sides at runtime.

**Pros:**
- Dot product equals cosine similarity without extra per-vector work.
- Consistent across backends (local or Matching Engine).
- Keeps runtime logic simple and predictable.

**Cons:**  
- Requires validation and guardrails during ingest and load.

**Decision:** Always normalize embeddings during ingest, validate norms at load, and normalize query vectors at request time.

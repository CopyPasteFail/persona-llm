# Design Decisions for Persona LLM Retrieval Pipeline

## 1. Two CVs, Two Roles (role:infra, role:product)

**What:** Keep the infra CV (DevOps/SRE/Platform) and the product CV (PM/TPM/PO) as separate documents. Tag all chunks from each with a single role: `role:infra` or `role:product`.  
**Alternatives considered:**
- Merge both CVs into one file.
- Keep multiple fine-grained role tags (`role:devops`, `role:sre`, `role:platform`, `role:pm`, `role:tpm`, `role:po`).

**Pros:**
- Simple mental model: two “modes” of the same persona.
- Easy to steer retrieval by role without brittle job-title semantics.
- No risk of cross-contamination unless a query is mixed.

**Cons:**
- Less granularity than per-title roles.

**Decision:** Use `role:infra` and `role:product` only. It’s the least complex way to keep answers consistent.

---

## 2. Optional Topic Tags (not role synonyms)

**What:** Add topic tags like `topic:kubernetes`, `topic:roadmap` for precision. Do not duplicate role synonyms.  
**Alternatives considered:** Encode many role synonyms as tags.

**Pros:**  
- Topics sharpen retrieval without overfitting to job titles.  
- Less tagging churn if role taxonomy changes.  

**Cons:** Requires discipline in tagging.

**Decision:** Use topics for precision; keep roles minimal.

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

**What:** Use a vector index to fetch semantically similar chunks quickly.  
**Alternative:** Brute-force cosine search in-process.  

**Pros:**  
- Scales well, millisecond retrieval.  
- Standard method for semantic recall.  

**Cons:** Requires embedding + upsert step.  

**Decision:** Use Vertex AI Matching Engine. Brute-force only viable at tiny scale.

---

## 5. Add BM25 (Keyword Signal)

**What:** Build a tiny inverted index at app startup and compute BM25 at query time.  
**Alternatives considered:** Don’t add BM25; or stand up Elasticsearch/OpenSearch.

**Pros:**  
- Catches acronyms, IDs, rare terms vectors may miss.  
- Zero infra; trivial CPU at this scale.  
- Example: query “experience with KEDA” → BM25 ensures chunks literally mentioning *KEDA* are promoted, even if embeddings underweight it.  

**Cons:** Slightly more code to blend scores.  

**Decision:** Add BM25 in-memory at startup. Chosen over prebuilding during ingestion and pulling from a bucket because the corpus is tiny, the index builds in milliseconds, and doing it at boot guarantees consistency with loaded chunks. Avoids managing another artifact, versioning, or cache invalidation. Precomputing only makes sense at large scale.

---

## 6. Hybrid Retrieval and Rerank (Wide → Narrow)

**What:** Pull ~50 ANN candidates, blend with BM25 + tag boosts, trim to ~8 for the LLM.  
**Alternative:** Fetch exactly 8 from ANN only.

**Pros:**  
- Wider pool reduces ANN misses.  
- Reranking improves relevance.  
- Hybrid = semantics (ANN) + exact matches (BM25).  
- Example: query “incident response metrics” → ANN finds SRE-related context, BM25 ensures chunks with exact phrase are not missed.  
- Final ~8 keeps prompt small.  

**Cons:** Slightly more CPU per query.  

**Decision:** Wide-then-rerank balances semantic breadth with keyword precision. Negligible cost at current scale.

---

## 7. Runtime Classification by Role

**What:** On each query, classify toward `infra` or `product` (keyword heuristic + optional embedding sim). Leave unclassified if mixed.  
**Alternatives:** Let LLM decide; or require user to pick a role.

**Pros:**  
- Cheap, low-latency.  
- Keeps persona consistent without user having to choose.  
- Mixed queries (e.g. “How do you prioritize infra work?”) can surface both product-style and infra chunks.  
- Prevents wasting LLM tokens on irrelevant role-specific content.  

**Cons:** Small logic layer to maintain.  

**Decision:** Add a local classifier; bias retrieval softly. Hard filters only if explicitly requested.

---

## 8. Build BM25 Index at Startup

**What:** Recompute inverted index at container boot.  
**Alternative:** Precompute BM25 at ingestion and load from GCS.

**Pros:**  
- Always consistent with loaded chunks.  
- Millisecond build time at this size.  
- Avoids versioning/caching complexity.  

**Cons:** Startup time could grow if corpus grows.  

**Decision:** Build at startup now; precompute only if scale demands.

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

**What:** Keep structured fields in the chunks JSONL (e.g., `role: "infra" | "product"`, `topics: [...]`) **and** also generate flattened `tags` (e.g., `["role:infra","topic:kubernetes"]`). Push **only `tags`** into the vector DB metadata for fast filtering; keep the full structured fields in the sidecar JSONL for validation, analytics, and provenance.

**Alternatives considered:**
- **Tags only** (no structured fields): keep just `["role:infra","topic:kubernetes"]`.
- **Structured only** (no tags): keep `role`/`topics` but don’t denormalize to `tags`.

**Pros:**
- **At retrieval**: vector DBs (like Vertex AI Matching Engine) filter fastest on flat metadata. `tags` make ANN filtering simple and efficient.
- **Data quality**: structured fields give schema guarantees (e.g., `role ∈ {infra, product}`), avoiding typos like `role:infraa`.
- **Analytics & ops**: structured fields are easier to aggregate (“how many infra chunks?”, “topic coverage?”) and evolve (add new fields) without regexing strings.
- **Clarity**: humans can read `role`/`topics` at a glance; `tags` are a runtime convenience.

**Cons:**
- **Duplication**: `tags` repeat information that’s already in `role`/`topics`.
- **Slightly more ingestion logic**: must auto-generate tags from structured fields.

**Decision:** **Keep both.**  
- Generate `tags` automatically at ingestion from `role` + `topics` (deterministic, no manual maintenance).  
- **Push only `tags`** (plus `chunk_id`) to the vector index for fast ANN filtering.  
- Keep **structured fields** in the JSONL sidecar for validation, analytics, and future evolution.

**Example (ingested chunk):**
```json
{
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-001",
  "role": "infra",
  "topics": ["kubernetes","terraform"],
  "tags": ["role:infra","topic:kubernetes","topic:terraform"],
  "text": "Ran EKS with Terraform and ArgoCD."
}
```

**Query usage:**
- ANN call with metadata filter: `tags CONTAINS "role:infra"`.
- Rerank boost: `tags CONTAINS "topic:kubernetes"`.
- Analytics (outside ANN): group by `role`, count by `topics`.

---

## 12. Chunk Identity vs Order (chunk_id and position)

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

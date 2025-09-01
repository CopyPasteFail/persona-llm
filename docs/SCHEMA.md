# PersonaChunk Schema - Field Guide

This guide explains each field: what it signifies, how the app uses it, the benefits, an example, and what happens if you drop it.

---

## Core Identity

### `schema_version` (integer; const: 2)
- **Meaning**: Version tag for this schema.
- **Use**: Lets the backend branch logic/migrations safely.
- **Benefit**: Future evolution without breaking old data.
- **Example**: `2`
- **If dropped**: Harder to migrate or validate mixed versions.

### `doc_id` (string)
- **Meaning**: Stable ID for the source document.
- **Use**: Group chunks; show provenance (“CV-infra-2025”).
- **Benefit**: Clean updates, deduping, auditing.
- **Example**: `"cv-infra-2025"`
- **If dropped**: Can’t reliably group or attribute chunks to a doc.

### `chunk_id` (string)
- **Meaning**: Unique ID per chunk (primary key for retrieval).
- **Use**: Key in vector index; join back to text/metadata.
- **Benefit**: Stable citations and debugging.
- **Example**: `"infra-014"`
- **If dropped**: Can’t map ANN hits back to exact text.

### `position` (integer >= 1)
- **Meaning**: Order of this chunk within the doc.
- **Use**: Reassemble adjacent chunks; preserve sequence in citations.
- **Benefit**: Multi-chunk answers remain coherent.
- **Example**: `14`
- **If dropped**: Adjacent evidence may appear out of order.

---

## Content

### `text` (string)
- **Meaning**: The actual chunked sentence(s) from the CV.
- **Use**: Embeddings (vectors) + BM25 keyword scoring + LLM context.
- **Benefit**: Grounded answers with minimal hallucinations.
- **Example**: `"Defined SLOs and built Prometheus + Grafana dashboards."`
- **If dropped**: No content to embed, rank, or cite.

---

## Role & Topics

### `role` (enum: `infra` | `product`)
- **Meaning**: Collapsed persona mode for this chunk.
- **Use**: Retrieval biasing (infra vs product) at query-time.
- **Benefit**: One persona voice, precise evidence selection.
- **Example**: `"infra"`
- **If dropped**: Harder to steer retrieval; more cross-contamination.

### `topics` (array<string>)
- **Meaning**: Normalized topical hints (lowercase, consistent).
- **Use**: Boosts/filters; analytics (“coverage by topic”).
- **Benefit**: Precision without brittle job titles.
- **Example**: `["kubernetes","terraform","observability"]`
- **If dropped**: Less fine-grained control; reliance on raw text only.

### `tags` (array<string>)
- **Meaning**: Flattened metadata for fast vector-DB filtering.
- **Use**: ANN pre-filter (e.g., tags contains `role:infra`).
- **Benefit**: Efficient, simple metadata filters in the vector store.
- **Example**: `["role:infra","topic:kubernetes","topic:terraform"]`
- **If dropped**: Must fetch broadly then post-filter in backend (slower, costlier).

---

## CV Context & Time

### `section` (string)
- **Meaning**: CV section where this chunk comes from.
- **Use**: Retrieval bias (“prefer Experience for ‘what did you do?’”), provenance in citations.
- **Benefit**: Trust and readability (“Experience → SRE @ Acme”).
- **Example**: `"Experience"`
- **If dropped**: Citations lose context; less precise retrieval.

### `start_year` / `end_year` (integers; optional)
- **Meaning**: Time span; **single-year** = same value for both.
- **Use**: Recency bias/filters; better provenance in citations.
- **Benefit**: Answers show *when* you did something.
- **Example**: `2019` / `2021` (or `2020` / `2020`)
- **If dropped**: Harder to answer or sort by time; weaker citations.

---

## Operational & Provenance

### `lang` (string)
- **Meaning**: Language code (e.g., `"en"`).
- **Use**: Optional filter; future multilingual support.
- **Benefit**: Control which language feeds the LLM.
- **Example**: `"en"`
- **If dropped**: Harder to handle multilingual corpora later.

### `updated_at` (ISO datetime)
- **Meaning**: When this chunk was generated/last touched.
- **Use**: Recency heuristics; cache invalidation.
- **Benefit**: Prefer fresher content when relevant.
- **Example**: `"2025-09-02T10:00:00Z"`
- **If dropped**: No recency signals or audit trail.

### `source_uri` (URI)
- **Meaning**: Pointer to original source (e.g., GCS path).
- **Use**: Deep provenance; debugging; re-chunking.
- **Benefit**: Trace every chunk to a file.
- **Example**: `"gs://bucket/cv-infra-2025.pdf"`
- **If dropped**: Harder to fix or verify sources.

### `permissions` (array<string>)
- **Meaning**: ACL-style labels (e.g., `"team:eng"`).
- **Use**: Filter restricted chunks at retrieval time.
- **Benefit**: Safe multi-tenant or public demos.
- **Example**: `["public"]` or `["team:eng","internal"]`
- **If dropped**: No access control via metadata.

---

## Extras (optional, human-friendly)

### `extras.employer` (string)
- **Meaning**: Employer/company for this chunk.
- **Use**: Display in citations; employer-specific queries.
- **Benefit**: Cleaner “Experience → SRE @ Acme (2019–2021)” labels.
- **Example**: `"Acme Inc."`
- **If dropped**: Less specific provenance in answers.

### `extras.tech` (array<string>)
- **Meaning**: Raw, human-friendly tech names as written in the CV.
- **Use**: UI display (“Tech: Kubernetes (K8s), Terraform, Argo CD”).
- **Benefit**: Polished presentation separate from normalized `topics`.
- **Example**: `["Kubernetes (K8s)","Terraform","Argo CD"]`
- **If dropped**: You can still rely on `text` for raw wording, but you lose the easy, pretty, de-duplicated tech list.

### `extras.type` (enum: `achievement` | `experience`)
- **Meaning**: Semantic kind of item.
- **Use**: Weighting/rules (e.g., prioritize achievements).
- **Benefit**: Finer control for ranking/formatting.
- **Example**: `"achievement"`
- **If dropped**: Less control over how different lines are prioritized or shown.

---

## Example Chunk

```json
{
  "schema_version": 2,
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-014",
  "position": 14,
  "text": "Defined SLOs and built Prometheus + Grafana dashboards.",
  "role": "infra",
  "topics": ["observability","slo","grafana","prometheus"],
  "tags": ["role:infra","topic:observability","topic:slo","topic:grafana","topic:prometheus"],
  "section": "Experience",
  "start_year": 2019,
  "end_year": 2021,
  "lang": "en",
  "updated_at": "2025-09-02T10:00:00Z",
  "source_uri": "gs://bucket/cv-infra-2025.pdf",
  "permissions": ["public"],
  "extras": {
    "employer": "Acme Inc.",
    "tech": ["Prometheus","Grafana","SLOs"],
    "type": "experience"
  }
}

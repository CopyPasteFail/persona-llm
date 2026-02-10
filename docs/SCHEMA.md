# PersonaChunk Schema - Fields Guide

This guide explains each field: what it signifies, how the app uses it, the benefits, an example, and what happens if you drop it.

---

## Core Identity

### `schema_version` (integer; const: 2)
- **Meaning**: Version tag for this schema.
- **Use**: Lets the backend to handle logic/migrations safely.
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
- **See also**: [RATIONALE.md §12](./RATIONALE.md#12-chunk-identity-vs-order-chunk_id-and-position) for design trade-offs around chunk identity vs order.

### `position` (integer ≥ 1)
- **Meaning**: Order of this chunk within the doc.
- **Use**: Reassemble adjacent chunks; preserve sequence in citations.
- **Benefit**: Multi-chunk answers remain coherent.
- **Example**: `14`
- **If dropped**: Adjacent evidence may appear out of order.
- **See also**: [RATIONALE.md §12](./RATIONALE.md#12-chunk-identity-vs-order-chunk_id-and-position) for design trade-offs around chunk identity vs order.

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

### `extras.type` (enum: `achievement` | `experience`) — *Experience-only*
- **Meaning**: Semantic kind of Experience bullet (either a responsibility or a highlight).
- **Use**: Optional weighting/formatting (e.g., emphasize achievements).
- **Benefit**: Lets you distinguish results vs responsibilities within the Experience section.
- **Example**: `"achievement"`
- **Omit for**: **Summary** and **Skills** chunks (not applicable there).
- **If dropped**: No way to distinguish results vs responsibilities, which is fine if you don’t need that nuance.

---

## Examples

### Summary (omit `extras.type`)
```json
{
  "schema_version": 2,
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-001",
  "position": 1,
  "text": "DevOps/SRE engineer experienced in automating infrastructure, running Kubernetes in production, and improving observability.",
  "role": "infra",
  "topics": ["devops","sre","automation","kubernetes","observability"],
  "tags": ["role:infra","topic:devops","topic:sre","topic:automation","topic:kubernetes","topic:observability"],
  "section": "Summary",
  "start_year": 2025,
  "end_year": 2025,
  "lang": "en",
  "updated_at": "2025-09-02T20:00:00Z",
  "source_uri": "gs://bucket/cv-infra-2025.docx",
  "permissions": ["public"],
  "extras": {
    "employer": "",
    "tech": ["Kubernetes","Prometheus","Terraform"]
  }
}
```

### Skills (omit `extras.type`)
```json
{
  "schema_version": 2,
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-010",
  "position": 10,
  "text": "Skills: Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, GCP.",
  "role": "infra",
  "topics": ["kubernetes","terraform","argocd","prometheus","grafana","aws","gcp"],
  "tags": ["role:infra","topic:kubernetes","topic:terraform","topic:argocd","topic:prometheus","topic:grafana","topic:aws","topic:gcp"],
  "section": "Skills",
  "start_year": 2025,
  "end_year": 2025,
  "lang": "en",
  "updated_at": "2025-09-02T20:00:00Z",
  "source_uri": "gs://bucket/cv-infra-2025.docx",
  "permissions": ["public"],
  "extras": {
    "employer": "",
    "tech": ["Kubernetes","Terraform","Argo CD","Prometheus","Grafana","AWS","GCP"]
  }
}
```

### Experience (optionally set `extras.type`)
```json
{
  "schema_version": 2,
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-020",
  "position": 20,
  "text": "At Acme Inc., managed production EKS clusters with Terraform and Argo CD, improved monitoring with Prometheus and Grafana, and defined SLOs to improve reliability.",
  "role": "infra",
  "topics": ["eks","terraform","argocd","prometheus","grafana","slo","reliability"],
  "tags": ["role:infra","topic:eks","topic:terraform","topic:argocd","topic:prometheus","topic:grafana","topic:slo","topic:reliability"],
  "section": "Experience",
  "start_year": 2019,
  "end_year": 2021,
  "lang": "en",
  "updated_at": "2025-09-02T20:00:00Z",
  "source_uri": "gs://bucket/cv-infra-2025.docx",
  "permissions": ["public"],
  "extras": {
    "employer": "Acme Inc.",
    "tech": ["EKS","Terraform","Argo CD","Prometheus","Grafana"],
    "type": "experience"
  }
}

---


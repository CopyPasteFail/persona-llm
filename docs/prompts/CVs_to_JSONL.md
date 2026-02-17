# CVs → JSONL Conversion

You are converting ONE OR MORE ATTACHED CV FILES into JSONL lines that conform to the PersonaChunk schema.

## Files

* I will attach one or more CV files.
* Each file is a separate CV and must produce its own set of JSONL records.
* Infer the professional domain `profile` from the filename and CV content. Examples: "infra", "product", "marketing", "sales", "finance", "legal", "dentistry".
* Use a single `profile` value per CV. If the CV spans multiple domains, pick the primary profile and reflect the rest via `topics`.

## Required attachment gates

You must verify that attachments named exactly `chunk.schema.json` and `experience_domain_config.json` are present and readable.

If `chunk.schema.json` is not attached, or cannot be opened, STOP and output exactly this single line (and nothing else):
ERROR: Missing required attachment chunk.schema.json

If `experience_domain_config.json` is not attached, or cannot be opened, STOP and output exactly this single line (and nothing else):
ERROR: Missing required attachment experience_domain_config.json

If both are attached:
* Use `chunk.schema.json` as the source of truth for required fields, types, and allowed enum values.
* Parse `experience_domain_config.json` as JSON and load:
  * `allowed_stint_domains` = `experience_domain_config.json.canonical_labels` (array of strings).
* `allowed_stint_domains` is the only allowed value set for `extras.stint_domains` in Experience chunks.
If anything in this prompt conflicts with the JSON schema, the JSON schema wins.

## Output

* Output a plain JSONL file named `chunks.jsonl` (one JSON object per line).
* Each line must be a valid JSON object, no commas, no brackets, no Markdown.
* Deliver the result as a downloadable file, not inline text.

## Schema

* `schema_version`: must match `chunk.schema.json` exactly.
* `doc_id`: stable ID per CV, derived from profile and year when available, e.g. `"cv-infra-2025"`. Human-readable, not hashed.
* `chunk_id`: `<profile>-NNN` (sequential per CV, zero-padded to 3 digits).
* `position`: monotonically increasing across the whole CV (1..N).
* Use required fields from `chunk.schema.json`. Do not omit required fields.

## Chunking Rules

* Target ~150–250 tokens per chunk (~300–900 chars). Prefer ~500–700 chars.
* Hard max per chunk: ~900 chars. Never exceed this unless the original single bullet/sentence is longer (then split by sentence boundaries).
* Atomic unit = one bullet OR one sentence-level achievement/responsibility. Do not pack many bullets together.
* Pack adjacent bullets only if same employer/title/topic AND the resulting chunk stays under ~250 tokens (~900 chars) AND contains no more than 2–3 atomic facts.
* **Overlap**: Add ~1 sentence overlap within the same title/employer block (keep it small, do not exceed the ~900 char cap).
* **Never overlap across titles, employers, or across sections.**
* Convert to first person. Remove PII. Do not invent dates.

## Experience Coverage Rules

* Every bullet and every sentence under each dated Experience stint must appear in at least one chunk in the Experience section for that same stint.
* Do not move Experience bullets into Summary or Skills instead of representing them in Experience.
* Before outputting JSONL, internally verify that no Experience bullet was omitted. If any bullet is missing, create an additional Experience chunk for it.
* Do not print the verification.

## Experience stint_domains rules

For every chunk where `section == "Experience"`:
* `extras.stint_domains` is required and must be non-empty.
* Every value in `extras.stint_domains` must be one of `allowed_stint_domains`.
* Do not invent new stint domain labels.

### Edge case: industry or channel labels (repair, do not print)

Some CV content describes an industry or channel rather than a role-domain label (examples: "ecommerce", "e-commerce", "amazon", "fba", "fintech", "healthcare", "retail").
These must not appear in `extras.stint_domains`.

If you encounter such a label:
1) Move it into `topics` (normalized lowercase token).
2) Choose the closest matching canonical role labels from `allowed_stint_domains` for `extras.stint_domains`.
3) Keep `extras.stint_domains` non-empty.

Heuristic mapping for common cases:
* "ecommerce" / "amazon" / "fba" / "private label" → include `["sales","marketing","product"]` (add `"data"` only if analytics-heavy).
* "entrepreneurship" / "business owner" → include `["product","sales"]` (add `"marketing"` if relevant).

## Tags

* Always include `profile:<profile>`.
* For each topic, include `topic:<t>`.
* Tags = profile + topics only.

## Examples (from SCHEMA.md)

`schema_version` must equal the const in `backend/schema/chunk.schema.json`.
Example 1 uses `3` as an illustrative value; verify the schema file before generating output.

### Example 1: Summary (omit `extras.type`)

**Input (CV text):**\
"DevOps/SRE engineer experienced in automating infrastructure, running Kubernetes in production, and improving observability."

**Output JSONL:**

```json
{
  "schema_version": 3,
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-001",
  "position": 1,
  "text": "DevOps/SRE engineer experienced in automating infrastructure, running Kubernetes in production, and improving observability.",
  "profile": "infra",
  "topics": ["devops","sre","automation","kubernetes","observability"],
  "tags": ["profile:infra","topic:devops","topic:sre","topic:automation","topic:kubernetes","topic:observability"],
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

### Example 2: Skills (omit `extras.type`)

**Input (CV text):**\
"Skills: Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, GCP."

**Output JSONL:**

```json
{
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-010",
  "position": 10,
  "text": "Skills: Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, GCP.",
  "profile": "infra",
  "topics": ["kubernetes","terraform","argocd","prometheus","grafana","aws","gcp"],
  "tags": ["profile:infra","topic:kubernetes","topic:terraform","topic:argocd","topic:prometheus","topic:grafana","topic:aws","topic:gcp"],
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

### Example 3: Experience (with `extras.type`)

**Input (CV text):**\
"At Acme Inc., managed production EKS clusters with Terraform and Argo CD, improved monitoring with Prometheus and Grafana, and defined SLOs to improve reliability."

**Output JSONL:**

```json
{
  "doc_id": "cv-infra-2025",
  "chunk_id": "infra-020",
  "position": 20,
  "text": "At Acme Inc., managed production EKS clusters with Terraform and Argo CD, improved monitoring with Prometheus and Grafana, and defined SLOs to improve reliability.",
  "profile": "infra",
  "topics": ["eks","terraform","argocd","prometheus","grafana","slo","reliability"],
  "tags": ["profile:infra","topic:eks","topic:terraform","topic:argocd","topic:prometheus","topic:grafana","topic:slo","topic:reliability"],
  "section": "Experience",
  "start_year": 2019,
  "end_year": 2021,
  "lang": "en",
  "updated_at": "2025-09-02T20:00:00Z",
  "source_uri": "gs://bucket/cv-infra-2025.docx",
  "permissions": ["public"],
  "extras": {
    "employer": "Acme Inc.",
    "title": "Senior SRE",
    "stint_domains": ["platform","sre"],
    "tech": ["EKS","Terraform","Argo CD","Prometheus","Grafana"],
    "type": "experience"
  }
}
```

---

## File Structure Example

The output `chunks.jsonl` file contains one JSON object per line. Here is an example with two lines:

```jsonl
{"schema_version":3,"doc_id":"cv-infra-2025","chunk_id":"infra-001","position":1,"text":"DevOps/SRE engineer experienced in automating infrastructure, running Kubernetes in production, and improving observability.","profile":"infra","topics":["devops","sre","automation","kubernetes","observability"],"tags":["profile:infra","topic:devops","topic:sre","topic:automation","topic:kubernetes","topic:observability"],"section":"Summary","start_year":2025,"end_year":2025,"lang":"en","updated_at":"2025-09-02T20:00:00Z","source_uri":"gs://bucket/cv-infra-2025.docx","permissions":["public"],"extras":{"employer":"","tech":["Kubernetes","Prometheus","Terraform"]}}
{"doc_id":"cv-infra-2025","chunk_id":"infra-002","position":2,"text":"Skills: Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, GCP.","profile":"infra","topics":["kubernetes","terraform","argocd","prometheus","grafana","aws","gcp"],"tags":["profile:infra","topic:kubernetes","topic:terraform","topic:argocd","topic:prometheus","topic:grafana","topic:aws","topic:gcp"],"section":"Skills","start_year":2025,"end_year":2025,"lang":"en","updated_at":"2025-09-02T20:00:00Z","source_uri":"gs://bucket/cv-infra-2025.docx","permissions":["public"],"extras":{"employer":"","tech":["Kubernetes","Terraform","Argo CD","Prometheus","Grafana","AWS","GCP"]}}
```

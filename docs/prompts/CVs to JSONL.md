# CVs → JSONL Conversion

You are converting TWO ATTACHED CV FILES into JSONL lines that conform to the PersonaChunk schema.

## Files

* I will attach two files right now.
* File with name containing \["infra","sre","devops","platform"] → role=infra.
* File with name containing \["product","pm","tpm","po"] → role=product.
* If ambiguous, infer from content.

## Output

* Output a plain JSONL file named `chunks.jsonl` (one JSON object per line).
* Each line must be a valid JSON object, no commas, no brackets, no Markdown.
* Deliver the result as a downloadable file, not inline text.

## Schema

* `schema_version`: 2
* `doc_id`: stable ID per CV, e.g. `"cv-infra-2025"`. Human-readable, not hashed.
* `chunk_id`: `<role>-NNN` (sequential per doc, starting at 001).
* `position`: integer ≥1.
* `text`: first-person chunk content.
* `role`: `"infra"` or `"product"`.
* `topics`: normalized lowercase tokens.
* `tags`: mirror role + topics (for fast ANN filtering).
* `section`: "Experience","Projects","Education","Certifications","Skills","Summary","Publications","Awards".
* `start_year` / `end_year`: optional integers; same year = both equal.
* `lang`: "en".
* `updated_at`: one UTC ISO timestamp across all lines.
* `source_uri`: `"file://<filename>"`.
* `permissions`: \["public"].
* `extras`:

  * `employer`: company/institution if applicable.
  * `tech`: human-friendly names.
  * `type`: "experience" or "achievement", **only for Experience**.

## Chunking Rules

* Target \~450 tokens (\~2.2k chars).
* If any bullet **or paragraph** >2.2k chars, split by sentence boundaries (never mid-sentence).
* Atomic unit = bullet; pack adjacent bullets only if same employer/role/topic and under \~450 tokens.
* **Overlap**: Add \~10% overlap by sentence within the same role/employer block.
* **Never overlap across roles, employers, or across sections.**
* Convert to first person. Remove PII. Do not invent dates.

## Tags

* Always include `role:<role>`.
* For each topic, include `topic:<t>`.
* Tags = role + topics only.

## Examples (from SCHEMA.md)

### Example 1: Summary (omit `extras.type`)

**Input (CV text):**\
"DevOps/SRE engineer experienced in automating infrastructure, running Kubernetes in production, and improving observability."

**Output JSONL:**

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

### Example 2: Skills (omit `extras.type`)

**Input (CV text):**\
"Skills: Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, GCP."

**Output JSONL:**

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

### Example 3: Experience (with `extras.type`)

**Input (CV text):**\
"At Acme Inc., managed production EKS clusters with Terraform and Argo CD, improved monitoring with Prometheus and Grafana, and defined SLOs to improve reliability."

**Output JSONL:**

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
```

---


## File Structure Example

The output `chunks.jsonl` file contains one JSON object per line. Here is an example with two lines:

```jsonl
{"schema_version":2,"doc_id":"cv-infra-2025","chunk_id":"infra-001","position":1,"text":"DevOps/SRE engineer experienced in automating infrastructure, running Kubernetes in production, and improving observability.","role":"infra","topics":["devops","sre","automation","kubernetes","observability"],"tags":["role:infra","topic:devops","topic:sre","topic:automation","topic:kubernetes","topic:observability"],"section":"Summary","start_year":2025,"end_year":2025,"lang":"en","updated_at":"2025-09-02T20:00:00Z","source_uri":"gs://bucket/cv-infra-2025.docx","permissions":["public"],"extras":{"employer":"","tech":["Kubernetes","Prometheus","Terraform"]}}
{"schema_version":2,"doc_id":"cv-infra-2025","chunk_id":"infra-002","position":2,"text":"Skills: Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, GCP.","role":"infra","topics":["kubernetes","terraform","argocd","prometheus","grafana","aws","gcp"],"tags":["role:infra","topic:kubernetes","topic:terraform","topic:argocd","topic:prometheus","topic:grafana","topic:aws","topic:gcp"],"section":"Skills","start_year":2025,"end_year":2025,"lang":"en","updated_at":"2025-09-02T20:00:00Z","source_uri":"gs://bucket/cv-infra-2025.docx","permissions":["public"],"extras":{"employer":"","tech":["Kubernetes","Terraform","Argo CD","Prometheus","Grafana","AWS","GCP"]}}
```

Each line is a valid JSON object, and the file as a whole is line-delimited JSON (JSONL).

---

BEGIN: Read both attachments, then output the `chunks.jsonl` file.

# Vector Search Roles and Flows

## Backends (default: local)
- `VECTOR_BACKEND=local|matching_engine`
- **local**: in-process cosine search over the cached dataset embeddings. Zero serving cost and simplest for a POC; default path.
- **matching_engine**: Vertex AI Matching Engine for larger corpora or low-latency/high-recall needs. Requires `INDEX_ENDPOINT_ID` + `DEPLOYED_INDEX_ID`.
- Query embeddings are L2-normalized at request time; datapoints are pre-normalized at ingest, and the runtime validates norms.

## Roles (Matching Engine only)
- `INDEX_ID`: The vector index asset. Needed to deploy a new/updated index. Not used for queries/upserts once deployed.
- `INDEX_ENDPOINT_ID`: The serving endpoint. Stable “host” you deploy to and send traffic through.
- `DEPLOYED_INDEX_ID`: Your chosen name for a specific deployment on that endpoint; used to route queries/upserts. It must start with a letter and only include letters, numbers, or underscores.

## Dependency Breakdown

Here's a breakdown of the typical creation process and dependencies in a cloud vector search service (like Vertex AI Vector Search):

### 1. Creating the Index
- Action: Create a vector index (a data structure containing your vector embeddings).
- Inputs: Raw data, vector dimensions, algorithm type, etc.
- Output: `INDEX_ID` (the unique identifier for the stored vector data).
- Dependency: This step does not depend on the `INDEX_ENDPOINT_ID`.

### 2. Creating the Index Endpoint
- Action: Create an Index Endpoint (a network-accessible service where indexes can be deployed).
- Inputs: Display name, network configuration (e.g., VPC), and regional location.
- Output: `INDEX_ENDPOINT_ID` (the unique identifier for the endpoint).
- Dependency: This step does not depend on the `INDEX_ID`.

### 3. Deploying the Index
- Action: Deploy the vector index to the endpoint to make it queryable.
- Inputs:
  - The `INDEX_ID` (which index you want to deploy).
  - The `INDEX_ENDPOINT_ID` (where you want to deploy the index).
  - A unique `DEPLOYED_INDEX_ID` (a user-specified name for this specific deployment on the endpoint).
- Output: The index becomes available for querying via the endpoint's address.
- Dependency: This step has a direct dependency on both the `INDEX_ID` and the `INDEX_ENDPOINT_ID`. Both must be created before the deployment can occur.

## Dev Use Cases
- First setup: create index → create endpoint → deploy index with a `DEPLOYED_INDEX_ID`.
- Data refresh: upsert datapoints using `INDEX_ENDPOINT_ID` + `DEPLOYED_INDEX_ID` (no `INDEX_ID` needed).
- Blue/green swap: create a new index (`INDEX_ID`), deploy it to the same endpoint with a new `DEPLOYED_INDEX_ID`, test by pointing clients to the new ID, then undeploy the old.
- Parallel personas/envs: separate endpoints per persona/env, or share one endpoint and use different `DEPLOYED_INDEX_ID`s (clients must specify which to hit).

## When Required
- Deploy: `INDEX_ID` + `INDEX_ENDPOINT_ID` + `DEPLOYED_INDEX_ID`
- Upsert: `INDEX_ENDPOINT_ID` + `DEPLOYED_INDEX_ID`
- Query: `INDEX_ENDPOINT_ID` + `DEPLOYED_INDEX_ID`
- Cleanup: undeploy by `INDEX_ENDPOINT_ID` + `DEPLOYED_INDEX_ID`; delete index by `INDEX_ID`; delete endpoint by `INDEX_ENDPOINT_ID`

## Diagram
```mermaid
flowchart TD
  I[Index: INDEX_ID]
  E[Endpoint: INDEX_ENDPOINT_ID]
  D[Deployment: DEPLOYED_INDEX_ID]

  I -->|deploy| D
  D -->|hosted on| E

  U[Upsert] -->|endpoint + deployedIndexId| D
  Q[Query]  -->|endpoint + deployedIndexId| D

  BG[Blue/Green Swap] -->|new INDEX_ID -> new DEPLOYED_INDEX_ID| D
```

---

## Why not always use a different Deployed Index ID?

The `DEPLOYED_INDEX_ID` is the name of a specific route/instance running on a stable server (`INDEX_ENDPOINT_ID`). You generally want to keep this ID stable for two main reasons:

1. API/Application Stability: Your application code (the client) is configured to query a specific resource - the `INDEX_ENDPOINT_ID` and the `DEPLOYED_INDEX_ID` (e.g., `my_endpoint/production_route`). If you change the `DEPLOYED_INDEX_ID` every time you update, you must also update and redeploy the client application, which is cumbersome and risks downtime.
2. Stateless Deployment: The `DEPLOYED_INDEX_ID` primarily points to a configuration (replicas, machine type, etc.) and a specific `INDEX_ID` (the data). If you are just making an in-place update to the data (e.g., streaming new vectors), you don't need a new deployment name.

## When to Switch the ID

| Component to Change | The Index ID (The Data) | The Deployed Index ID (The Route/Instance) |
| :--- | :--- | :--- |
| What it Represents | The vector data and the physical structure (dimensions, algorithm, sharding). | The configuration of the deployment on the endpoint (e.g., replica count, machine type). |
| The Action is a... | Data Update or Structural Change | Deployment Configuration Change |
| Why Change It? | - You have a major, full data rebuild (e.g., re-running all embeddings). <br> - You are changing the core algorithm or vector dimension size. <br> - You are performing a zero-downtime blue/green deployment of a brand new Index (see below). | - You need to change replica counts (scale up/down). <br> - You need to change the machine type (if allowed by the platform). <br> - Never switch this ID for an update if your goal is client API stability. |
| Best Practice | Update the existing Index ID content in-place (for incremental/streaming updates). | Keep the Deployed Index ID stable and use it as a persistent route name. |

---

## The Key Scenarios (Switching `INDEX_ID` vs. `DEPLOYED_INDEX_ID`)

### 1. Switching the Index ID for a Zero-Downtime Deployment (Blue/Green)

This is the most critical workflow where you deliberately switch the underlying `INDEX_ID` while keeping the `DEPLOYED_INDEX_ID` stable.

- Goal: Replace the entire corpus of data with a new, fully rebuilt index without any interruption to live traffic.
- Steps:
  1. Traffic is hitting Endpoint A / Deployed ID: `v1-live` (this points to `INDEX_ID`: `I-v1`).
  2. You build a completely new index (`INDEX_ID`: `I-v2`).
  3. You use the service's mutate/swap operation to tell the existing Deployed ID `v1-live` to now point to `INDEX_ID`: `I-v2`.
  4. The system smoothly unloads `I-v1` and loads `I-v2` onto the live deployment resources, while the client continues to query the stable endpoint (`v1-live`).
- Result: The `INDEX_ID` changes from `I-v1` to `I-v2`, but the `DEPLOYED_INDEX_ID` remains a stable route (`v1-live`) that your application code never had to touch.

### 2. Keeping the `INDEX_ID` Stable for Incremental Updates

This is the standard use case for most running services.

- Goal: Add, update, or delete a few vectors.
- Action: Perform an Upsert (or Streaming Update) operation directly on the original `INDEX_ID` or its deployment.
- Result: The `INDEX_ID` remains the same, and the Deployed `INDEX_ID` remains the same. The deployed resources synchronize with the small, incremental data changes.

### 3. Switching the `DEPLOYED_INDEX_ID` (Less Common)

Only create a new `DEPLOYED_INDEX_ID` on the same Index Endpoint if you need a separate environment or a unique resource configuration.

- Scenario: You need a "staging" version of the index to test query results or performance on a smaller, cheaper machine before moving to the full production size.
- Steps:
  1. `DEPLOYED_INDEX_ID` `I-v1` to Endpoint A with Deployed ID `staging` (e.g., 2 replicas).
  2. Deploy the exact same Index ID `I-v1` to Endpoint A with Deployed ID `production` (e.g., 10 replicas).
- Result: Testing applications query `staging`, while live applications query `production`. The only difference is the deployment configuration, not the data itself.

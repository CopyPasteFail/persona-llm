.PHONY: install dev mock build fe-% fe-install be-install be-% require-private require-gcp-env require-index-ids require-datapoints-file require-operation-id clean clean-all gcp-create-project gcp-set-project gcp-create-bucket gcp-sa-create gcp-sa-delete gcp-sa-bind-roles gcp-sa-roles gcp-sa-key gcp-index-create gcp-index-endpoint-create gcp-index-deploy gcp-index-upsert gcp-index-list gcp-index-op-describe gcp-index-op-done gcp-index-update-time

# -------------------------------
# Private directory resolution
# Precedence: ENV > .privatedir > ./private
# -------------------------------

# Environment bootstrap
include make/env.mk

# (=) Deferring expansion
SA_EMAIL = persona-llm@$(PROJECT_ID).iam.gserviceaccount.com
SA_MEMBER = serviceAccount:$(SA_EMAIL)
BUCKET_URI = gs://$(BUCKET_NAME)
# Default path for generated service account key; override with `make gcp-sa-key KEY_FILE=/path/to/key.json`
KEY_FILE ?= $(PRIVATE_DIR)/secrets/key.json
ME_MIN_REPLICAS ?= 1
ME_MAX_REPLICAS ?= 1
# Accept either a bare endpoint ID or a full resource path
INDEX_ENDPOINT_URI = projects/$(PROJECT_ID)/locations/$(REGION)/indexEndpoints/$(INDEX_ENDPOINT_ID)

require-private:
	@test -d "$(PRIVATE_DIR)" || { echo "Missing PRIVATE_DIR=$(PRIVATE_DIR). Set PRIVATE_DIR, create .privatedir, or add ./private symlink."; exit 1; }
	@test -f "$(COMMON_ENV)" || { echo "Missing $(COMMON_ENV). Copy private-template/ and populate secrets/common.env."; exit 1; }
	@test -f "$(BACKEND_ENV)" || { echo "Missing $(BACKEND_ENV). Copy private-template/ and populate secrets/backend.env."; exit 1; }
	@test -f "$(FRONTEND_ENV)" || { echo "Missing $(FRONTEND_ENV). Copy private-template/ and populate secrets/frontend.env."; exit 1; }

require-gcp-env:
	@[ -n "$(PROJECT_ID)" ] || { echo "PROJECT_ID is missing"; exit 1; }
	@[ -n "$(REGION)" ] || { echo "REGION is missing"; exit 1; }
	@[ -n "$(BUCKET_NAME)" ] || { echo "BUCKET_NAME is missing"; exit 1; }

require-index-ids:
	@[ -n "$(INDEX_ENDPOINT_ID)" ] || { echo "INDEX_ENDPOINT_ID is missing"; exit 1; }
	@[ -n "$(INDEX_ID)" ] || { echo "INDEX_ID is missing"; exit 1; }
	@[ -n "$(DEPLOYED_INDEX_ID)" ] || { echo "DEPLOYED_INDEX_ID is missing"; exit 1; }

require-datapoints-file:
	@[ -n "$(DATAPOINTS_FILE)" ] || { echo "DATAPOINTS_FILE must be set (configure it in $(PRIVATE_DIR)/secrets/backend.env)"; exit 1; }
	@[ -f "$(DATAPOINTS_FILE)" ] || { echo "Missing datapoints file: $(DATAPOINTS_FILE)"; exit 1; }

require-operation-id:
	@[ -n "$(OPERATION_ID)" ] || { echo "OPERATION_ID must be set (copy it from gcloud ai indexes update output)"; exit 1; }

# ----- Frontend passthrough -----
fe-%:
	npm --prefix frontend run $*

fe-install:
	npm --prefix frontend install

# ----- Backend passthrough -----
be-%: require-private
	$(MAKE) -C backend $*

be-install: require-private
	$(MAKE) -C backend install

install:
	$(MAKE) fe-install
	$(MAKE) be-install

dev: require-private
	PERSONA_DIR="$${PERSONA_DIR:-$(PRIVATE_DIR)/persona}" $(MAKE) be-dev & \
	PRIVATE_DIR="$(PRIVATE_DIR)" $(MAKE) fe-dev

mock: require-private
	( $(MAKE) be-mock & PRIVATE_DIR="$(PRIVATE_DIR)" $(MAKE) fe-dev:mock )

build:
	$(MAKE) be-docker-build
	$(MAKE) fe-build

clean:
	$(MAKE) be-clean
	$(MAKE) fe-clean

clean-all:
	$(MAKE) be-clean-all
	$(MAKE) fe-clean:all

gcp-print-env: require-private require-gcp-env
	@echo "PROJECT_ID=$(PROJECT_ID)"
	@echo "BUCKET_NAME=$(BUCKET_NAME)"
	@echo "SA_EMAIL=$(SA_EMAIL)"

# Create a new GCP project
gcp-create-project: require-private require-gcp-env
	@gcloud projects create "$(PROJECT_ID)" --name="Persona LLM"
	@echo "⚠️ Remember to link a billing account to the project:"
	@echo "  gcloud beta billing projects link \"$(PROJECT_ID)\" --billing-account=YOUR_BILLING_ACCOUNT_ID"

# Set active GCP project and show billing info (warn if not linked)
gcp-set-project: require-gcp-env
	@gcloud config set project "$(PROJECT_ID)"
	@echo "Checking billing for project $(PROJECT_ID)..."
	@if billing_info="$$(gcloud beta billing projects describe "$(PROJECT_ID)" 2>/dev/null)"; then \
		echo "$$billing_info"; \
	else \
		echo "WARNING: Project $(PROJECT_ID) does not have billing linked."; \
	fi

# Create bucket "gs://$(BUCKET_NAME)" in REGION
gcp-create-bucket: require-private require-gcp-env
	@gcloud storage buckets create "gs://$(BUCKET_NAME)" --location="$(REGION)"

# Enable Firebase features on the active GCP project
gcp-enable-firebase: require-gcp-env
	@gcloud alpha firebase projects add-firebase "$(PROJECT_ID)"

gcp-index-create: require-private require-gcp-env
	@tmp="$$(mktemp)"; \
	printf '%s\n' \
	  '{' \
	  '  "config": {' \
	  '    "dimensions": $(if $(DATAPOINTS_DIMENSIONS),$(DATAPOINTS_DIMENSIONS),3072),' \
	  '    "distanceMeasureType": "DOT_PRODUCT_DISTANCE",' \
	  '    "approximateNeighborsCount": 100,' \
	  '    "algorithmConfig": {' \
	  '      "treeAhConfig": {' \
	  '        "leafNodeEmbeddingCount": 1000,' \
	  '        "leafNodesToSearchPercent": 7' \
	  '      }' \
	  '    }' \
	  '  }' \
	  '}' \
	  > "$$tmp"; \
	gcloud ai indexes create \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)" \
		--display-name="persona-index" \
		--metadata-file="$$tmp"; \
	rm -f "$$tmp"
	@echo "Capture the INDEX_ID from the output (projects/.../indexes/ID)."

gcp-index-endpoint-create: require-private require-gcp-env
	@gcloud ai index-endpoints create \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)" \
		--display-name="persona-endpoint"
	@echo "Capture the INDEX_ENDPOINT_ID from the output (projects/.../indexEndpoints/ID)."

gcp-index-deploy: require-private require-gcp-env require-index-ids
	@gcloud ai index-endpoints deploy-index "$(INDEX_ENDPOINT_URI)" \
		--deployed-index-id="$(DEPLOYED_INDEX_ID)" \
		--display-name="persona-deployment" \
		--index="$(INDEX_ID)" \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)" \
		--min-replica-count="$(ME_MIN_REPLICAS)" \
		--max-replica-count="$(ME_MAX_REPLICAS)"

gcp-index-upsert: require-private require-gcp-env require-index-ids require-datapoints-file
	@set -e; \
	stamp="$$(date +%Y%m%d-%H%M%S)"; \
	tmp_dir="$$(mktemp -d)"; \
	convert_target="$$tmp_dir/datapoints.json"; \
	case "$(DATAPOINTS_FILE)" in \
	  *.jsonl.gz) \
	    gzip -dc "$(DATAPOINTS_FILE)" > "$$convert_target" ;; \
	  *.jsonl) \
	    cp "$(DATAPOINTS_FILE)" "$$convert_target" ;; \
	  *.json) \
	    cp "$(DATAPOINTS_FILE)" "$$convert_target" ;; \
	  *) \
	    echo "Unsupported datapoints extension: $(DATAPOINTS_FILE). Supported: .jsonl, .jsonl.gz, .json"; \
	    rm -rf "$$tmp_dir"; \
	    exit 1 ;; \
	esac; \
	object_dir="gs://$(BUCKET_NAME)/matching-engine/$$stamp/"; \
	echo "Uploading datapoints to $${object_dir}datapoints.json"; \
	gsutil cp "$$convert_target" "$${object_dir}" >/dev/null; \
	metadata_tmp="$$(mktemp)"; \
	printf '{\n  "contentsDeltaUri": "%s"\n}\n' "$${object_dir}" > "$$metadata_tmp"; \
	echo "Triggering batch index update (this can take several minutes)..."; \
	operation_id="$$(gcloud ai indexes update "$(INDEX_ID)" \
	  --region="$(REGION)" \
	  --project="$(PROJECT_ID)" \
	  --metadata-file="$$metadata_tmp" \
	  --format='value(name)' \
	  --quiet)"; \
	rm -f "$$metadata_tmp"; \
	rm -rf "$$tmp_dir"; \
	if [ -n "$$operation_id" ]; then \
	  echo "Index update request submitted."; \
	  echo "Track progress with:"; \
	  echo "  gcloud ai operations describe $$operation_id --index=$(INDEX_ID) --region=$(REGION) --project=$(PROJECT_ID)"; \
	else \
	  echo "Index update request submitted. Monitor progress in Cloud Console."; \
	fi

gcp-index-list: require-gcp-env
	@gcloud ai indexes list --region="$(REGION)" --project="$(PROJECT_ID)"

gcp-index-op-describe: require-gcp-env require-operation-id
	@gcloud ai operations describe "$(OPERATION_ID)" \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)"

gcp-index-op-errors: require-gcp-env require-operation-id
	@gcloud ai operations describe "$(OPERATION_ID)" \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)" \
		--format=json \
		| jq 'def stripvec: if has("rawRecord") and (.rawRecord != null) then .rawRecord = ((.rawRecord | fromjson? // {} | del(.featureVector)) | tojson) else . end; .metadata.nearestNeighborSearchOperationMetadata.contentValidationStats |= (map(.partialErrors |= ((. // []) | map(stripvec)))) | {done, error, contentValidationStats: .metadata.nearestNeighborSearchOperationMetadata.contentValidationStats}'

gcp-index-op-done: require-gcp-env require-operation-id
	@gcloud ai operations describe "$(OPERATION_ID)" \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)" \
		--format='value(done)'

gcp-index-update-time: require-gcp-env require-index-ids
	@gcloud ai indexes describe "$(INDEX_ID)" \
		--region="$(REGION)" \
		--project="$(PROJECT_ID)" \
		--format='value(updateTime)'

# Show whether billing is linked for the active project
gcp-check-billing: require-gcp-env
	@gcloud beta billing projects describe "$(PROJECT_ID)" --format='value(billingEnabled)'

# Link billing account to the active project
gcp-link-billing: require-gcp-env
	@if [ -z "$(BILLING_ACCOUNT_ID)" ]; then \
	  echo "BILLING_ACCOUNT_ID must be set"; \
	  exit 1; \
	fi
	@gcloud beta billing projects link "$(PROJECT_ID)" --billing-account="$(BILLING_ACCOUNT_ID)"

# Create service account and bind roles
gcp-sa-create: require-private require-gcp-env
	@gcloud iam service-accounts create persona-llm --display-name="Persona LLM"

# Delete the SA (idempotent)
gcp-sa-delete: require-gcp-env
	@set -e; \
	SA_EMAIL="persona-llm@$(PROJECT_ID).iam.gserviceaccount.com"; \
	echo "Deleting $$SA_EMAIL from $(PROJECT_ID) ..."; \
	gcloud iam service-accounts delete "$$SA_EMAIL" --project "$(PROJECT_ID)" --quiet || true

# Bind roles for building stage
gcp-sa-grant-builder: require-private require-gcp-env
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.admin"
	@gcloud storage buckets add-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectCreator"
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebasehosting.admin"
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebase.admin"

# Revoke roles for building stage
gcp-sa-revoke-builder: require-private require-gcp-env
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.admin" --quiet || true
	@gcloud storage buckets remove-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectCreator" --quiet || true
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebasehosting.admin" --quiet || true
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebase.admin" --quiet || true

# Bind roles for runtime stage
gcp-sa-grant-runtime: require-private require-gcp-env
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.user"
	@gcloud storage buckets add-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectViewer"

# Revoke roles for runtime stage
gcp-sa-revoke-runtime: require-private require-gcp-env
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.user" --quiet || true
	@gcloud storage buckets remove-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectViewer" --quiet || true

# Show only LIVE roles for the SA (project + bucket)
gcp-sa-roles: require-private require-gcp-env
	@set -eu; \
	SA_EMAIL="persona-llm@$(PROJECT_ID).iam.gserviceaccount.com"; \
	if gcloud iam service-accounts describe "$$SA_EMAIL" --project "$(PROJECT_ID)" --format="yaml" >/dev/null 2>&1; then \
	  echo "== SA describe ($(PROJECT_ID)) =="; \
	  gcloud iam service-accounts describe "$$SA_EMAIL" --project "$(PROJECT_ID)" --format="yaml"; \
	else \
	  echo "== SA describe ($(PROJECT_ID)) =="; \
	  echo "Service account $$SA_EMAIL does not exist in project $(PROJECT_ID)"; \
	fi; \
	echo; \
	echo "== Project IAM entries (live only) =="; \
	OUT="$$(gcloud projects get-iam-policy "$(PROJECT_ID)" \
	  --flatten="bindings[].members" \
	  --format="table(bindings.role, bindings.members)" \
	  | awk '$$2 ~ /^serviceAccount:/')"; \
	if [ -n "$$OUT" ]; then echo "$$OUT"; else echo "None"; fi; \
	echo; \
	echo "== Bucket IAM entries for gs://$(BUCKET_NAME) (live only) =="; \
	OUT="$$(gcloud storage buckets get-iam-policy "gs://$(BUCKET_NAME)" \
	  --format="table(bindings.role, bindings.members)" \
	  | awk '$$2 ~ /^serviceAccount:/')"; \
	if [ -n "$$OUT" ]; then echo "$$OUT"; else echo "None"; fi

# Create a service account key (defaults to $(PRIVATE_DIR)/secrets/key.json)
gcp-sa-key: require-private require-gcp-env
	@install -d "$(dir $(KEY_FILE))"
	@gcloud iam service-accounts keys create "$(KEY_FILE)" \
	  --iam-account "persona-llm@$(PROJECT_ID).iam.gserviceaccount.com"

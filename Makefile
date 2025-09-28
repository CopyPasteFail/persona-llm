.PHONY: install dev mock build fe-% fe-install be-install be-% require-private require-gcp-env clean clean-all gcp-create-project gcp-set-project gcp-create-bucket gcp-sa-create gcp-sa-delete gcp-sa-bind-roles gcp-sa-roles gcp-sa-key

# -------------------------------
# Private directory resolution
# Precedence: ENV > .privatedir > ./private
# -------------------------------

# If PRIVATE_DIR is not provided by the environment, resolve it.
ifeq ($(origin PRIVATE_DIR), undefined)
  ifneq ("$(wildcard .privatedir)","")
    PRIVATE_DIR := $(shell cat .privatedir)
  else
    PRIVATE_DIR := $(abspath private)
  endif
endif
export PRIVATE_DIR

# Helpers
BACKEND_ENV  := $(PRIVATE_DIR)/secrets/backend.env
FRONTEND_ENV := $(PRIVATE_DIR)/secrets/frontend.env

-include $(BACKEND_ENV) # Ignore if doesn't exist
-include $(FRONTEND_ENV) # Ignore if doesn't exist

# (=) Deferring expansion
SA_EMAIL = persona-llm@$(PROJECT_ID).iam.gserviceaccount.com
SA_MEMBER = serviceAccount:$(SA_EMAIL)
BUCKET_URI = gs://$(BUCKET_NAME)

require-private:
	@test -d "$(PRIVATE_DIR)" || { echo "Missing PRIVATE_DIR=$(PRIVATE_DIR). Set PRIVATE_DIR, create .privatedir, or add ./private symlink."; exit 1; }
	@test -f "$(BACKEND_ENV)" || { echo "Missing $(BACKEND_ENV). Copy private-template/ and populate secrets/backend.env."; exit 1; }
	@test -f "$(FRONTEND_ENV)" || { echo "Missing $(FRONTEND_ENV). Copy private-template/ and populate secrets/frontend.env."; exit 1; }

require-gcp-env:
	@[ -n "$(PROJECT_ID)" ] || { echo "PROJECT_ID is missing"; exit 1; }
	@[ -n "$(REGION)" ] || { echo "REGION is missing"; exit 1; }
	@[ -n "$(FIREBASE_PROJECT_ID)" ] || { echo "FIREBASE_PROJECT_ID is missing"; exit 1; }
	@[ -n "$(BUCKET_NAME)" ] || { echo "BUCKET_NAME is missing"; exit 1; }

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

# --- revoke helpers ---
gcp-sa-revoke-builder: require-private require-gcp-env
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.admin" --quiet || true
	@gcloud storage buckets remove-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectCreator" --quiet || true
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebasehosting.admin" --quiet || true
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebase.admin" --quiet || true

gcp-sa-revoke-runtime: require-private require-gcp-env
	@gcloud projects remove-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.user" --quiet || true
	@gcloud storage buckets remove-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectViewer" --quiet || true

# Bind roles for building stage
gcp-sa-grant-builder: require-private require-gcp-env
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.admin"
	@gcloud storage buckets add-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectCreator"
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebasehosting.admin"
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/firebase.admin"

# Bind roles for runtime stage
gcp-sa-grant-runtime: require-private require-gcp-env
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" --member="$(SA_MEMBER)" --role="roles/aiplatform.user"
	@gcloud storage buckets add-iam-policy-binding "$(BUCKET_URI)" --member="$(SA_MEMBER)" --role="roles/storage.objectViewer"

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

# Create a key in PRIVATE_DIR/key.json
gcp-sa-key: require-private require-gcp-env
	@install -d "$(PRIVATE_DIR)"
	@gcloud iam service-accounts keys create "$(PRIVATE_DIR)/secrets/key.json" \
	  --iam-account "persona-llm@$(PROJECT_ID).iam.gserviceaccount.com"

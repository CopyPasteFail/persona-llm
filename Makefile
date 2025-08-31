.PHONY: install dev mock build fe-% fe-install be-% be-install require-private require-gcp-env clean clean-all gcp:set-project gcp:create-bucket gcp:sa:create gcp:sa:bind-roles gcp:sa:key

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

# Convenience: secrets paths
BACKEND_ENV  := $(PRIVATE_DIR)/secrets/backend.env
FRONTEND_ENV := $(PRIVATE_DIR)/secrets/frontend.env

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

# Set active GCP project
gcp:set-project: require-private require-gcp-env
	@gcloud config set project "$(PROJECT_ID)"

# Create bucket "gs://$(BUCKET_NAME)" in REGION
gcp:create-bucket: require-private require-gcp-env
	@gcloud storage buckets create "gs://$(BUCKET_NAME)" --location="$(REGION)"

# Create service account and bind roles
gcp:sa:create: require-private require-gcp-env
	@gcloud iam service-accounts create persona-llm --display-name="Persona LLM"

# Bind roles
gcp:sa:bind-roles: require-private require-gcp-env
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" \
	  --member="serviceAccount:persona-llm@$(PROJECT_ID).iam.gserviceaccount.com" \
	  --role="roles/aiplatform.user"
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" \
	  --member="serviceAccount:persona-llm@$(PROJECT_ID).iam.gserviceaccount.com" \
	  --role="roles/storage.objectAdmin"
	@gcloud projects add-iam-policy-binding "$(PROJECT_ID)" \
	  --member="serviceAccount:persona-llm@$(PROJECT_ID).iam.gserviceaccount.com" \
	  --role="roles/aiplatform.admin"

# Create a key in PRIVATE_DIR/key.json
gcp:sa:key: require-private require-gcp-env
	@install -d "$(PRIVATE_DIR)"
	@gcloud iam service-accounts keys create "$(PRIVATE_DIR)/key.json" \
	  --iam-account "persona-llm@$(PROJECT_ID).iam.gserviceaccount.com"

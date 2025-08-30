.PHONY: install dev mock build fe-% be-% require-private

# ENV_DIR points to the private folder (override per run if needed)
ENV_DIR ?= $(abspath private)

# Required files
BACKEND_ENV := $(ENV_DIR)/secrets/backend.env
FRONTEND_ENV := $(ENV_DIR)/secrets/frontend.env

export ENV_DIR

require-private:
	@test -d "$(ENV_DIR)" || { echo "Missing ENV_DIR=$(ENV_DIR)"; exit 1; }
	@test -f "$(BACKEND_ENV)" || { echo "Missing $(BACKEND_ENV)"; exit 1; }
	@test -f "$(FRONTEND_ENV)" || { echo "Missing $(FRONTEND_ENV)"; exit 1; }

# ----- Frontend passthrough -----
fe-%:
	npm --prefix frontend run $*

fe-install:
	npm --prefix frontend install

# ----- Backend passthrough -----
# We do not pass file paths here. Backend code reads ENV_DIR itself.
be-%: require-private
	$(MAKE) -C backend $*

# ----- Orchestration -----
install:
	$(MAKE) fe-install
	$(MAKE) be-install

dev: require-private
	PERSONA_DIR="$${PERSONA_DIR:-$(ENV_DIR)/persona}" $(MAKE) be-dev & \
	ENV_DIR="$(ENV_DIR)" $(MAKE) fe-dev

mock: require-private
	( $(MAKE) be-mock & ENV_DIR="$(ENV_DIR)" $(MAKE) fe-dev:mock )

build:
	$(MAKE) be-docker-build
	$(MAKE) fe-build

clean:
	$(MAKE) be-clean
	$(MAKE) fe-clean

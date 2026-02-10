.PHONY: install dev mock build fe-% be-% require-private clean clean-all

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

# ----- Frontend passthrough -----
fe-%:
	npm --prefix frontend run $*

fe-install:
	npm --prefix frontend install

# ----- Backend passthrough -----
# We do not pass file paths here. Backend code reads PRIVATE_DIR itself.
be-%: require-private
	$(MAKE) -C backend $*

# ----- Orchestration -----
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

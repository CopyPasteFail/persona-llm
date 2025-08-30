.PHONY: install dev mock build fe-% be-%

# ----- Generic frontend passthrough -----
# Usage: make fe-dev, make fe-dev:mock, make fe-build, etc.
fe-%:
	npm --prefix frontend run $*

fe-install:
	npm --prefix frontend install

# ----- Generic backend passthrough -----
# Usage: make be-dev, make be-test, etc. (delegates to backend/Makefile)
be-%:
	$(MAKE) -C backend $*

# ----- Orchestration -----
install:
	$(MAKE) fe-install
	$(MAKE) be-install

# Dev: load env from private if present, else fall back
dev:
	@set -a; [ -f private/secrets/backend.env ] && . private/secrets/backend.env || true; set +a; \
	PERSONA_DIR="$${PERSONA_DIR:-private/persona}" $(MAKE) be-dev & \
	NEXT_PUBLIC_API_URL="$${NEXT_PUBLIC_API_URL:-http://localhost:8080}" $(MAKE) fe-dev

# Mock: always use localhost
mock:
	( $(MAKE) be-mock & $(MAKE) fe-dev:mock )


build:
	$(MAKE) be-docker-build
	$(MAKE) fe-build

clean:
	$(MAKE) be-clean
	$(MAKE) fe-clean

    # Root Makefile for monorepo
    # After cloning, run:
    #   git submodule init
    #   git submodule update

    .PHONY: add-private install dev dev:mock build

    add-private:
    	@if [ -z "$$PRIVATE_REMOTE" ]; then echo "Set PRIVATE_REMOTE=git@github.com:<you>/persona-llm-private.git"; exit 1; fi
    	git submodule add -b main $$PRIVATE_REMOTE private || true
    	git submodule update --init --recursive

    install:
    	npm install
    	python3.13 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt || true

    dev:
    	( cd backend && ./run-dev.sh || make dev || true ) & \
		( cd web && npm run dev )

    dev:mock:
    	( cd backend && make mock || true ) & \
		( cd web && npm run dev:mock )

    build:
    	( cd backend && make docker-build || true ) & \
    	( cd web && npm run build )

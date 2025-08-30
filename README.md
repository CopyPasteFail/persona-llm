# persona-llm public monorepo

Public monorepo for the persona demo. It contains the frontend in `web` and the backend in `backend`. Persona data and secrets live in a private submodule named `private` that you host yourself.

## Layout

    persona-llm/
    ├─ web/                  # Next.js app
    ├─ backend/              # FastAPI app
    ├─ private-template/     # Example layout for your private repo
    │  ├─ persona/
    │  │  ├─ persona.yaml
    │  │  ├─ starters.json
    │  │  ├─ assets/
    │  │  └─ vector-seed/
    │  └─ secrets.example/
    │     ├─ backend.env.example
    │     └─ frontend.env.example
    ├─ .gitmodules           # Placeholder submodule config
    ├─ .gitignore
    └─ Makefile

## Private repo as a submodule

Create your own private repo and copy `private-template/` into it. Then wire it here as a submodule.

    git submodule add -b main git@github.com:YOUR_USER/persona-llm-private.git private
    git submodule update --init --recursive

Or use the Makefile helper:

    make add-private PRIVATE_REMOTE=git@github.com:YOUR_USER/persona-llm-private.git

Your private repo will contain:
- `persona/` with `persona.yaml`, `starters.json`, optional assets and seed docs
- `secrets/` with real `.env` files or SOPS encrypted files

## Install and run

    make install
    make dev

`make dev:mock` is available if the backend supports it.

## Notes

- Update `.gitmodules` with your real private repo URL.
- Push this monorepo to GitHub.
- In CI, enable checkout of submodules so builds can include `private/persona` at image build time.
- If you already load persona files via `PERSONA_DIR`, point it at `private/persona` in your backend env.

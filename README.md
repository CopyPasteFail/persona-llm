# persona-llm

Monorepo for the persona demo. Frontend in `web`, backend in `backend`. Persona data and secrets live in a private submodule at `private/`.

## Quick start

```bash
git submodule update --init --recursive   # after you add your private repo
make install
make dev
```

## Repo layout

- [`web/`](./frontend/README.md) — Next.js app, scripts and env vars
- [`backend/`](./backend/README.md) — FastAPI app, env vars, API docs
- `private/` — your private submodule (not included here). See below.
- `private-template/` — example structure for your private repo

## Private submodule

Create a private repo from `private-template/`, then add it:

```bash
git submodule add -b main https://github.com/YOUR_USER/YOUR_PRIVATE_REPO_NAME.git private
git submodule update --init --recursive
```

Or use the Makefile helper:

```bash
make add-private PRIVATE_REMOTE=git@github.com:YOUR_USER/persona-llm-private.git
```

Your private repo will contain:
- `persona/` with `persona.yaml`, `starters.json`, optional assets and seed docs
- `secrets/` with real `.env` files or SOPS encrypted files

## Develop

- Run both apps: `make dev`
- Mock mode if available: `make dev:mock`
- Build: `make build`

More:
- Frontend scripts: [`web/README.md`](./web/README.md)
- Backend commands and API: [`backend/README.md`](./backend/README.md)

## Notes

- Update `.gitmodules` with your real private repo URL.
- Push this monorepo to GitHub.
- In CI, enable checkout of submodules so builds can include `private/persona` at image build time.
- If you already load persona files via `PERSONA_DIR`, point it at `private/persona` in your backend env.

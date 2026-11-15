# PingPoint NOC: agent instructions

Self-hosted network operations centre (collect, store, visualise, alert). Python 3.13, FastAPI/uvicorn, SQLite (WAL),
pysnmp, vanilla JS (no CDN), systemd services. Public repository (MIT): nothing specific to a real environment goes in here.

## Start of a session

1. Read `docs/TRASPASO.md` (hand-over note, in Spanish; the maintainer says "quiero seguir" to resume), the section
   "Audit of 2026-11-14" in `ROADMAP.md`, and `README.md`.
2. Private operational notes (addresses, hostnames, deployment, audit plans) live outside this repository. Ask the
   maintainer if you need them, and never copy them into the repo.

## Commands

- Demo: `python -m tools.seed_demo --db-path data/demo.db`, then
  `python -m api --db-path data/demo.db --host 127.0.0.1 --port 8080`.
- Tests: `python -m pytest tests -n 4 -o addopts="-q"` (includes `tests/browser`; the whole suite takes about 8 minutes).
  Run it before calling anything done, and chain the commit with `&&`, without `tail` in between. A browser layout test
  can fail under load and pass when run alone.
- After touching `web/static/js`: `python tools/build_bundle.py` and `node --check web/static/app.js`.

## Rules

- Commits: author and committer are whoever configured `git config --local user.name` and `user.email`. No
  `Co-Authored-By` lines or tool attribution. Before pushing, check
  `git log --format='%an <%ae> | %cn <%ce>' origin/main..HEAD`.
- No push, tag, release or deployment unless the maintainer asks for it.
- No addresses, hostnames, keys or tokens of any real site in the repo; they belong in the server configuration. The
  admin token is never printed.
- Do not read `.env`, key or token files. If a secret value is needed, copy it to the clipboard
  (`... | Set-Clipboard`) instead of showing it.
- Do not run destructive commands against a live server without an explicit OK. The reference deployment procedure is in
  the private notes.
- README and public docs are in English; some older files in `docs/` are still in Spanish.
- Do not weaken authentication or input validation. The audit of 2026-11-14 lists known gaps (enrolment token, trusted
  proxy headers, security headers); fix them rather than working around them.

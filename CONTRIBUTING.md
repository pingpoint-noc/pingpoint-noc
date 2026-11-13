# Contributing to PingPoint NOC

Thanks for helping. A few ground rules keep the project easy to maintain.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .
```

The browser tests in `tests/browser` are optional and skip themselves unless Playwright is installed
(`pip install playwright` and `python -m playwright install chromium`).

## Workflow

- Branch from `main`, open a pull request, and keep each one focused on a single change.
- Write the test first and make it fail for the right reason, then make it pass.
- Build tests on real captures from `samples/`; if a sample is missing, capture a real one instead of inventing a format.
- Code, comments, commit messages and new docs are written in English. Existing technical docs in `docs/` are still
  partly in Spanish; translations are welcome.
- Commit messages follow Conventional Commits (`feat(web): ...`, `fix(alerts): ...`, `docs: ...`).

## Security

- Never commit secrets, real SNMP communities or keys, tokens, or private IP plans (`.env` and generated router scripts
  are ignored).
- Report vulnerabilities privately to the maintainers instead of opening a public issue.

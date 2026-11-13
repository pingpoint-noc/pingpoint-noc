# Security policy

PingPoint NOC is an early release meant for **trusted networks**. Report vulnerabilities privately to the maintainers
(GitHub security advisories) instead of opening a public issue.

## Known limitations (tracked, not yet fixed)

- **Read endpoints are public unless you turn the login on.** With `MININOC_AUTH_READ=1` every `GET` under `/api` needs a
  session (the shipped systemd unit sets it; the code default is off so existing setups do not change): the web shows a
  sign-in first, and the first visit creates the administrator (if the server has `MININOC_ADMIN_TOKEN`, that token is
  asked for too, so only whoever installed it can claim the account). The session cookie is `HttpOnly` and
  `SameSite=Strict` and only opens reads; writes still need the `Authorization` header. Without the flag, almost every
  `GET` is public: deploy behind an authenticating reverse proxy, or only on a trusted network. Open on purpose:
  `/healthz`, `/api/auth-status`, `/api/me`, and the host/router scripts (`bootstrap.sh`, `router-config.rsc`...).
- **Enrolment endpoints.** `POST /api/nodes/register` and `/api/nodes/register-proxmox` are called by booting hosts.
  Set `MININOC_ENROLL_TOKEN` on the server and export the same value on the host before running the bootstrap
  one-liner (it is sent as `X-Enroll-Token`). Without the token, only loopback and private addresses are accepted.
  `X-Forwarded-For` is ignored unless `MININOC_TRUST_PROXY=1` (set it only behind a proxy you control).
- **First-admin bootstrap.** `POST /api/bootstrap-admin` is open until the first admin exists, but only from a local
  (loopback or private) address and, when `MININOC_ADMIN_TOKEN` is set, only with that token. Create the admin right
  after installing. Behind a reverse proxy every visitor looks local, so set the token.
- **No TLS.** The API speaks plain HTTP; put it behind a TLS-terminating reverse proxy. The `bootstrap.sh` one-liner
  downloads the agent over the same channel.
- **Unpinned dependencies.** `requirements.txt` uses lower bounds without a lockfile.

Inputs that end up inside generated RouterOS scripts (`/api/router-config.rsc`) are validated; see
`tools/generate_router_config.py`.

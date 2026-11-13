# Security policy

PingPoint NOC is an early release meant for **trusted networks**. Report vulnerabilities privately to the maintainers
(GitHub security advisories) instead of opening a public issue.

## Known limitations (tracked, not yet fixed)

- **Read endpoints are public.** Almost every `GET` under `/api` (devices, syslog, flows, topology, settings) needs no
  login; writes require an admin session. Deploy behind a reverse proxy with authentication, or only on a trusted network.
- **Enrolment endpoints.** `POST /api/nodes/register` and `/api/nodes/register-proxmox` are called by booting hosts.
  Set `MININOC_ENROLL_TOKEN` on the server and export the same value on the host before running the bootstrap
  one-liner (it is sent as `X-Enroll-Token`). Without the token, only loopback and private addresses are accepted.
  `X-Forwarded-For` is ignored unless `MININOC_TRUST_PROXY=1` (set it only behind a proxy you control).
- **First-admin bootstrap.** `POST /api/bootstrap-admin` is open until the first admin exists, but only from a local
  (loopback or private) address. Create the admin right after installing.
- **No TLS.** The API speaks plain HTTP; put it behind a TLS-terminating reverse proxy. The `bootstrap.sh` one-liner
  downloads the agent over the same channel.
- **Unpinned dependencies.** `requirements.txt` uses lower bounds without a lockfile.

Inputs that end up inside generated RouterOS scripts (`/api/router-config.rsc`) are validated; see
`tools/generate_router_config.py`.

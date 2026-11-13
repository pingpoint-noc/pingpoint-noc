# Security policy

PingPoint NOC is an early release meant for **trusted networks**. Report vulnerabilities privately to the maintainers
(GitHub security advisories) instead of opening a public issue.

## Known limitations (tracked, not yet fixed)

- **Read endpoints are public.** Almost every `GET` under `/api` (devices, syslog, flows, topology, settings) needs no
  login; writes require an admin session. Deploy behind a reverse proxy with authentication, or only on a trusted network.
- **Enrolment endpoints have no login.** `POST /api/nodes/register` and `/api/nodes/register-proxmox` are meant to be
  called by a booting host. Do not expose the API to untrusted networks; a shared enrolment token is planned.
- **First-admin bootstrap.** `POST /api/bootstrap-admin` is open until the first admin exists. Create the admin
  immediately after installing, before exposing the service.
- **No TLS.** The API speaks plain HTTP; put it behind a TLS-terminating reverse proxy. The `bootstrap.sh` one-liner
  downloads the agent over the same channel.
- **Unpinned dependencies.** `requirements.txt` uses lower bounds without a lockfile.

Inputs that end up inside generated RouterOS scripts (`/api/router-config.rsc`) are validated; see
`tools/generate_router_config.py`.

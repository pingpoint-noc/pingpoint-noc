"""Provisioning, auto-bootstrap, and passive discovery inventory routes.

Exposes:
- GET /api/bootstrap.sh: 1-command Linux container/host onboarding script.
- GET /api/router-config.rsc: Idempotent MikroTik RouterOS (.rsc) telemetry script.
- POST /api/nodes/register: Self-registration endpoint called by bootstrap.sh.
- GET /api/nodes: Discovered and registered node inventory with protocol capabilities.
"""

import os
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

import scenario
from api import services
from tools.generate_router_config import build_rsc_content

# Only characters that are safe inside the double quotes of the generated bash script.
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$|^[0-9a-fA-F:.]+$")


class NodeRegistrationPayload(BaseModel):
    """JSON body sent by `bootstrap.sh` when a Linux host or container boots."""

    ip: str
    hostname: str = ""
    device_type: str = "linux"


class DiskTelemetryItem(BaseModel):
    label: str = "/"
    used_pct: float
    total_bytes: int | None = None
    used_bytes: int | None = None


class ServiceTelemetryItem(BaseModel):
    name: str
    status: str = "active"
    description: str = ""


class NodeMetricsPayload(BaseModel):
    cpu_load_pct: float | None = None
    loadavg_1m: float | None = None
    loadavg_5m: float | None = None
    loadavg_15m: float | None = None
    memory_used_pct: float | None = None
    mem_total_bytes: int | None = None
    mem_used_bytes: int | None = None
    mem_free_bytes: int | None = None
    uptime_seconds: int | None = None
    disks: list[DiskTelemetryItem] = []


class NodeTelemetryPayload(BaseModel):
    device_id: str
    hostname: str = ""
    ts_utc: int | None = None
    metrics: NodeMetricsPayload = NodeMetricsPayload()
    services: list[ServiceTelemetryItem] = []


BOOTSTRAP_TEMPLATE = Path(__file__).with_name("bootstrap.sh.in")
BOOTSTRAP_PVE_TEMPLATE = Path(__file__).with_name("bootstrap_pve.sh.in")


def build_bootstrap_script(noc_ip, noc_port=8080):
    """Build the bash onboarding script for Linux hosts and containers from api/routes/bootstrap.sh.in.

    The collector address was already checked against HOST_RE by the caller (nothing here can close the quotes).
    """
    if not HOST_RE.match(str(noc_ip)):
        raise ValueError("noc_ip must be an IP address or a host name")
    text = BOOTSTRAP_TEMPLATE.read_text(encoding="utf-8")
    return text.replace("@NOC_IP@", str(noc_ip)).replace("@NOC_PORT@", str(int(noc_port)))


def build_bootstrap_pve_script(noc_ip, noc_port=8080):
    """Build the bash onboarding script for Proxmox VE from api/routes/bootstrap_pve.sh.in."""
    if not HOST_RE.match(str(noc_ip)):
        raise ValueError("noc_ip must be an IP address or a host name")
    text = BOOTSTRAP_PVE_TEMPLATE.read_text(encoding="utf-8")
    return text.replace("@NOC_IP@", str(noc_ip)).replace("@NOC_PORT@", str(int(noc_port)))


class ProxmoxRegistrationPayload(BaseModel):
    """JSON body sent by `bootstrap-pve.sh` when a Proxmox VE hypervisor is onboarded."""

    ip: str
    hostname: str = ""
    token_id: str
    token_secret: str
    node_name: str = "proxmox"


def default_noc_ip():
    """Collector address offered when the request names none and has no usable host (MININOC_NOC_IP)."""
    return scenario.value("MININOC_NOC_IP")


def create_bootstrap_router(repo, clock):
    """Create the FastAPI APIRouter for onboarding and node discovery."""
    router = APIRouter(tags=["Provisioning"])

    @router.get("/api/bootstrap.sh")
    def get_bootstrap_script(
        request: Request,
        noc_ip: str | None = None,
        noc_port: int | None = None,
    ):
        resolved_ip = default_noc_ip()
        if noc_ip is not None and noc_ip.strip() != "":
            resolved_ip = noc_ip.strip()
        elif request.base_url.hostname:
            resolved_ip = request.base_url.hostname

        resolved_port = 8080
        if noc_port is not None:
            resolved_port = int(noc_port)
        elif request.base_url.port:
            resolved_port = int(request.base_url.port)

        if not HOST_RE.match(resolved_ip):
            raise HTTPException(status_code=400, detail="noc_ip must be an IP address or a host name")
        script = build_bootstrap_script(resolved_ip, resolved_port)
        return Response(content=script, media_type="text/x-shellscript; charset=utf-8")

    @router.get("/api/bootstrap-pve.sh")
    def get_bootstrap_pve_script(
        request: Request,
        noc_ip: str | None = None,
        noc_port: int | None = None,
    ):
        resolved_ip = default_noc_ip()
        if noc_ip is not None and noc_ip.strip() != "":
            resolved_ip = noc_ip.strip()
        elif request.base_url.hostname:
            resolved_ip = request.base_url.hostname

        resolved_port = 8080
        if noc_port is not None:
            resolved_port = int(noc_port)
        elif request.base_url.port:
            resolved_port = int(request.base_url.port)

        if not HOST_RE.match(resolved_ip):
            raise HTTPException(status_code=400, detail="noc_ip must be an IP address or a host name")
        script = build_bootstrap_pve_script(resolved_ip, resolved_port)
        return Response(content=script, media_type="text/x-shellscript; charset=utf-8")

    @router.get("/api/mininoc-agent.sh")
    def get_agent_script():
        agent_path = Path(__file__).resolve().parent.parent.parent / "tools" / "mininoc-agent.sh"
        if not agent_path.is_file():
            raise HTTPException(status_code=404, detail="Agent script not found")
        content = agent_path.read_text(encoding="utf-8")
        return Response(content=content, media_type="text/x-shellscript; charset=utf-8")

    @router.get("/api/router-config.rsc")
    def get_router_config_rsc(
        request: Request,
        noc_ip: str | None = None,
        community: str = "mininoc",
    ):
        resolved_ip = default_noc_ip()
        if noc_ip is not None and noc_ip.strip() != "":
            resolved_ip = noc_ip.strip()
        elif request.base_url.hostname and request.base_url.hostname != "testserver":
            resolved_ip = request.base_url.hostname

        try:
            rsc_text = build_rsc_content(noc_ip=resolved_ip, community=community)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return Response(
            content=rsc_text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="setup_router.rsc"'},
        )

    @router.post("/api/nodes/register")
    def register_node(payload: NodeRegistrationPayload):
        try:
            record = services.register_node_in_inventory(
                repo=repo,
                ip=payload.ip,
                hostname=payload.hostname,
                device_type=payload.device_type,
                now_ts=clock(),
            )
            # The endpoint has no login (a booting host calls it): the SNMP community stays inside the server.
            record.pop("snmp_community", None)
            return record
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/nodes/register-proxmox")
    def register_proxmox_node(payload: ProxmoxRegistrationPayload):
        try:
            record = services.register_proxmox_node_in_inventory(
                repo=repo,
                ip=payload.ip,
                hostname=payload.hostname,
                token_id=payload.token_id,
                token_secret=payload.token_secret,
                node_name=payload.node_name,
                now_ts=clock(),
            )
            return record
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/nodes/telemetry")
    def ingest_node_telemetry(
        payload: NodeTelemetryPayload,
        request: Request,
        x_node_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        token = x_node_token
        if not token and authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]

        if not token:
            raise HTTPException(status_code=401, detail="Missing X-Node-Token or Bearer Authorization header")

        secret = repo.get_session_secret()
        device = repo.get_device(payload.device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device " + str(payload.device_id) + " not found")

        global_tok = os.environ.get("MININOC_TELEMETRY_TOKEN", "")
        if not services.verify_node_telemetry_token(secret, device["id"], device["ip"], token, global_token=global_tok):
            raise HTTPException(status_code=401, detail="Invalid node telemetry token")

        client_ip = ""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        try:
            now_ts = int(payload.ts_utc) if payload.ts_utc is not None else clock()
            result = services.record_node_telemetry(
                repo=repo,
                payload_dict=payload.model_dump(),
                client_ip=client_ip,
                now_ts=now_ts,
            )
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/api/nodes")
    def list_nodes():
        return services.get_nodes_view(repo, clock())

    return router


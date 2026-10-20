"""Native HTTP REST client for Proxmox VE API using urllib (zero external dependencies).

Supports API Tokens (preferred for NOC monitoring) and Tickets, with configurable
SSL certificate verification.
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request


LOGGER = logging.getLogger("mini_noc.proxmox_client")


class ProxmoxApiError(Exception):
    """Base exception for Proxmox VE API interactions."""


class ProxmoxAuthError(ProxmoxApiError):
    """Raised when authentication fails (HTTP 401/403 or invalid token/ticket)."""


class ProxmoxUnreachableError(ProxmoxApiError):
    """Raised when the Proxmox host cannot be reached (network timeout, connection refused)."""


class ProxmoxApiClient:
    """Lightweight REST client for Proxmox VE /api2/json."""

    def __init__(
        self,
        host,
        port=8006,
        token_id=None,
        token_secret=None,
        ticket=None,
        csrf_token=None,
        verify_ssl=False,
        ca_cert_path=None,
        timeout=5.0,
    ):
        raw_host = str(host).strip()
        if raw_host.startswith("https://") or raw_host.startswith("http://"):
            parsed = urllib.parse.urlparse(raw_host)
            hostname = parsed.hostname or raw_host
            port_to_use = parsed.port or port
        else:
            hostname = raw_host
            port_to_use = port

        self.host = hostname
        self.port = int(port_to_use)
        self.base_url = f"https://{self.host}:{self.port}/api2/json"

        self.token_id = str(token_id).strip() if token_id else None
        self.token_secret = str(token_secret).strip() if token_secret else None
        self.ticket = str(ticket).strip() if ticket else None
        self.csrf_token = str(csrf_token).strip() if csrf_token else None

        self.verify_ssl = bool(verify_ssl)
        self.ca_cert_path = ca_cert_path
        self.timeout = float(timeout)

        self._ssl_context = self._create_ssl_context()

    def _create_ssl_context(self):
        """Build an SSLContext based on verification preferences."""
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        if self.ca_cert_path:
            return ssl.create_default_context(cafile=self.ca_cert_path)

        return ssl.create_default_context()

    def get_headers(self):
        """Construct required HTTP headers for Proxmox authentication."""
        headers = {
            "User-Agent": "Mini-NOC-Proxmox-Client/1.0",
            "Accept": "application/json",
        }

        # API Token authentication: PVEAPIToken=USER@REALM!TOKENID=SECRET
        if self.token_id and self.token_secret:
            headers["Authorization"] = f"PVEAPIToken={self.token_id}={self.token_secret}"
        elif self.ticket:
            headers["Cookie"] = f"PVEAuthCookie={self.ticket}"
            if self.csrf_token:
                headers["CSRFPreventionToken"] = self.csrf_token

        return headers

    def get(self, endpoint, params=None):
        """Perform an HTTP GET request to `endpoint` and return decoded 'data' field."""
        clean_ep = endpoint if endpoint.startswith("/") else "/" + endpoint
        url = self.base_url + clean_ep

        if params:
            query = urllib.parse.urlencode(params)
            url += "?" + query

        req = urllib.request.Request(url, headers=self.get_headers(), method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as resp:
                raw_bytes = resp.read()
                text = raw_bytes.decode("utf-8", errors="replace")
                data = json.loads(text)
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data

        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProxmoxAuthError(f"HTTP {exc.code} authentication failed for {url}") from exc
            raise ProxmoxApiError(f"HTTP {exc.code} error from {url}: {exc.reason}") from exc

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProxmoxUnreachableError(f"Failed to connect to Proxmox at {url}: {exc}") from exc

        except json.JSONDecodeError as exc:
            raise ProxmoxApiError(f"Invalid JSON response from {url}: {exc}") from exc


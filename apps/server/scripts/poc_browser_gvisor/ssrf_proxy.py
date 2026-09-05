"""PoC host-side SSRF filtering proxy — the D10 egress chokepoint.

A tiny forward proxy (HTTP + CONNECT) that Chromium points at via
``--proxy-server``. It runs in the container's MAIN netns (which has real
internet), while the sandbox lives in an isolated netns whose ONLY route out is
this proxy. Every request is DNS-resolved here and refused unless every resolved
address is globally routable — so private / loopback / link-local (incl.
169.254.169.254 cloud metadata) targets are blocked at the network egress point,
which a sandbox-internal raw socket cannot bypass (there is no other route).

Self-contained (stdlib only) on purpose: the PoC must not import product code.
The product proxy will reuse ``agentcore.core.net`` (classify_url / ip_is_safe /
pinned DNS) instead of this simplified re-implementation. Decisions are logged as
``PROXY_DECISION=...`` JSON lines so the harness can assert the SSRF verdicts.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import select
import socket
import sys
import threading
from socketserver import StreamRequestHandler, ThreadingTCPServer

_BLOCKED_HOSTNAMES = {"localhost", "0.0.0.0", "metadata.google.internal"}

# Clash/Mihomo fake-IP placeholder range (RFC 2544). On a dev machine behind such
# a proxy, public names resolve to 198.18.x.x and the local proxy routes them to
# the real host. ``core/net.py`` allows these under ``web_fetch_allow_fake_ip_proxy``;
# the PoC mirrors that so the happy path (proxy → real internet) is testable here.
# True private / loopback / link-local targets stay blocked regardless.
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
_ALLOW_FAKE_IP = True


def _log(kind: str, **fields: object) -> None:
    sys.stdout.write(f"PROXY_{kind}=" + json.dumps(fields, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _is_fake_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _FAKE_IP_NET
    except ValueError:
        return False


def _ip_is_safe(ip: str) -> bool:
    if _ALLOW_FAKE_IP and _is_fake_ip(ip):
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local  # incl. 169.254.169.254 cloud metadata
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_safe(host: str) -> tuple[str | None, str]:
    """Resolve ``host`` and return (pinned_ip, reason). pinned_ip None ⇒ blocked."""
    h = host.strip().rstrip(".").lower()
    if not h or h in _BLOCKED_HOSTNAMES or h.endswith(".local") or h.endswith(".internal"):
        return None, "blocked_host"
    try:
        ipaddress.ip_address(h)
        return (h, "ok_literal") if _ip_is_safe(h) else (None, "private_ip")
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None, "dns_fail"
    addrs = list(dict.fromkeys(i[4][0] for i in infos))
    if not addrs:
        return None, "dns_fail"
    if not all(_ip_is_safe(a) for a in addrs):
        return None, "private_ip"
    return addrs[0], "ok"


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Bidirectional byte tunnel until either side closes."""
    socks = [a, b]
    try:
        while True:
            r, _, x = select.select(socks, [], socks, 60)
            if x or not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return


class Handler(StreamRequestHandler):
    def handle(self) -> None:
        try:
            line = self.rfile.readline(65536).decode("latin-1").strip()
        except OSError:
            return
        if not line:
            return
        parts = line.split(" ")
        if len(parts) < 3:
            return
        method, target = parts[0], parts[1]
        if method.upper() == "CONNECT":
            self._connect(target)
        else:
            self._forward(method, target, parts[2])

    def _reply(self, status: str) -> None:
        """A bodyless HTTP reply that closes cleanly (no keep-alive hang)."""
        self.wfile.write(
            f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("latin-1")
        )
        self.wfile.flush()

    def _drain_headers(self) -> None:
        while True:
            h = self.rfile.readline(65536)
            if h in (b"\r\n", b"\n", b""):
                break

    def _connect(self, target: str) -> None:
        host, _, port_s = target.rpartition(":")
        port = int(port_s or "443")
        self._drain_headers()
        ip, reason = _resolve_safe(host)
        if ip is None:
            _log("DECISION", method="CONNECT", host=host, allowed=False, reason=reason)
            self._reply("403 Forbidden")
            return
        _log("DECISION", method="CONNECT", host=host, ip=ip, port=port, allowed=True, reason=reason)
        try:
            upstream = socket.create_connection((ip, port), timeout=15)
        except OSError as exc:
            self._reply("502 Bad Gateway")
            _log("UPSTREAM_FAIL", host=host, ip=ip, error=str(exc))
            return
        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self.wfile.flush()
        _pump(self.connection, upstream)
        upstream.close()

    def _forward(self, method: str, url: str, version: str) -> None:
        # Plain-HTTP absolute-form request (proxy form). Minimal: resolve + relay.
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or 80
        ip, reason = _resolve_safe(host)
        if ip is None:
            _log("DECISION", method=method, host=host, allowed=False, reason=reason)
            self._drain_headers()
            self._reply("403 Forbidden")
            return
        _log("DECISION", method=method, host=host, ip=ip, port=port, allowed=True, reason=reason)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        headers = []
        while True:
            h = self.rfile.readline(65536)
            if h in (b"\r\n", b"\n", b""):
                break
            headers.append(h)
        try:
            upstream = socket.create_connection((ip, port), timeout=15)
        except OSError as exc:
            self._reply("502 Bad Gateway")
            _log("UPSTREAM_FAIL", host=host, ip=ip, error=str(exc))
            return
        req = f"{method} {path} {version}\r\n".encode("latin-1") + b"".join(headers) + b"\r\n"
        upstream.sendall(req)
        _pump(self.connection, upstream)
        upstream.close()


class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8888)
    args = ap.parse_args()
    server = Server((args.host, args.port), Handler)
    _log("READY", host=args.host, port=args.port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        t.join()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

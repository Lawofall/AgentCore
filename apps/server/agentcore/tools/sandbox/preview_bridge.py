"""In-guest TCP bridge: 0.0.0.0:bridge_port → 127.0.0.1:app_port.

Copied onto the desk scratch bind as ``/scratch/preview_bridge.py`` and launched
with allowlisted ``python3`` (path stays under ``/scratch``). Guest loopback is
not sandboxd's loopback; this process is what sandboxd dials.
"""

from __future__ import annotations

import contextlib
import socket
import sys
import threading


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)


def _handle(client: socket.socket, app_port: int) -> None:
    try:
        upstream = socket.create_connection(("127.0.0.1", app_port))
    except OSError:
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
    _pipe(upstream, client)
    with contextlib.suppress(OSError):
        client.close()
    with contextlib.suppress(OSError):
        upstream.close()


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: preview_bridge.py BRIDGE_PORT APP_PORT")
    bridge_port = int(argv[1])
    app_port = int(argv[2])
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", bridge_port))
    server.listen(128)
    while True:
        client, _addr = server.accept()
        threading.Thread(target=_handle, args=(client, app_port), daemon=True).start()


if __name__ == "__main__":
    main(sys.argv)

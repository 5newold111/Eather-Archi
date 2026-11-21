"""試験用の偽サーバーの共通部品（決定 173）。ポートは 19500〜19599 の空いているものを使う"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTS = range(19500, 19600)


def free_port() -> int:
    for port in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("19500〜19599 に空いているポートがありません")


def serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, int]:
    """handler を別スレッドで動かす。(サーバー, ポート)。終わったら server.shutdown(); server.server_close()"""
    last = None
    for _ in range(20):
        port = free_port()
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError as e:   # 取ろうとした間にほかの試験が使った
            last = e
    else:
        raise RuntimeError(str(last))
    srv.daemon_threads = True
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True).start()   # 止めるときに待たないよう、短い間隔で
    return srv, port

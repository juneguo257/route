#!/usr/bin/env python3
"""Serve the Route map from saved exports without collecting fuel prices."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def build_map(directory):
    directory.mkdir(parents=True, exist_ok=True)
    retailer_file = directory / "stations.json"
    try:
        retailers = json.loads(retailer_file.read_text())
    except (OSError, json.JSONDecodeError):
        retailers = None
    encoded = json.dumps(retailers).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ROOT / "web" / "index.html").read_text()
    (directory / "index.html").write_text(template.replace("__ROUTE_DATA__", encoded))
    for asset in ("map.js", "data.js", "style.css"):
        shutil.copyfile(ROOT / "web" / asset, directory / asset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "output", help="Folder containing gasbuddy/stations.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--build-only", action="store_true", help="Update frontend assets without starting a server")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    directory = args.directory.resolve()
    build_map(directory)
    if args.build_only:
        print(f"Built {directory / 'index.html'}")
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(directory)))
    print(f"Map: http://127.0.0.1:{args.port} · reading saved exports only", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

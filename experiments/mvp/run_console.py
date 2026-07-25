"""Launch Merlin Console on the local loopback interface."""

from __future__ import annotations

import argparse
import threading
import webbrowser

from .console import create_console_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Merlin Console controlled-sample beta.")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects an empty ephemeral port.")
    parser.add_argument("--open", action="store_true", help="Open the Console in the default browser.")
    args = parser.parse_args(argv)
    server = create_console_server(port=args.port)
    print("Merlin Console beta")
    print(f"listening -> {server.base_url}")
    print("scope -> fixed controlled sample; press Control-C to stop")
    if args.open:
        threading.Timer(0.15, lambda: webbrowser.open(server.base_url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("stopping -> temporary session cleaned")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

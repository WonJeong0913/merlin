"""Launch Merlin's chat-first, account-free Build Week judge demo."""

from __future__ import annotations

import argparse
import threading
import webbrowser

from .judge_chat import create_judge_chat_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects an empty port.")
    parser.add_argument("--open", action="store_true", help="Open the chat in the default browser.")
    args = parser.parse_args(argv)
    server = create_judge_chat_server(port=args.port)
    print("Merlin chat-first judge demo")
    print(f"listening -> {server.base_url}")
    print("scope -> account-free golden incident; press Control-C to stop")
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

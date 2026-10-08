"""Run the demo UI on 127.0.0.1 only: `python -m web [--port 8765]`."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="web")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    mode = "live (" + app.state.metaforge.model + ")" if app.state.metaforge.live_llm else "offline"
    print(f"metaforge UI ({mode}): http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

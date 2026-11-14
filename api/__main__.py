"""CLI entry point for the Mini-NOC FastAPI service (python -m api)."""

import argparse
import os

import uvicorn

from api.app import create_app


def build_parser():
    """Return the argument parser for the API server."""
    parser = argparse.ArgumentParser(description="Mini-NOC HTTP API service")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"),
        help="Path to the SQLite database file (opened in read-only mode)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MININOC_API_HOST", "0.0.0.0"),
        help="Listen address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MININOC_API_PORT", "8080")),
        help="Listen TCP port (default: 8080)",
    )
    return parser


def main(argv=None):
    """Parse arguments and run the uvicorn HTTP server."""
    parser = build_parser()
    args = parser.parse_args(argv)
    app = create_app(args.db_path)
    # One access line per request (the web refreshes every few seconds) fills the journal for nothing: off unless asked for
    uvicorn.run(app, host=args.host, port=args.port, access_log=os.environ.get("MININOC_API_ACCESS_LOG", "0") == "1")


if __name__ == "__main__":
    main()


"""Bundle individual JS modules from web/static/js into web/static/app.js.

Allows developers and students to work on modular, clean files (100-300 lines each)
while preserving single-file static delivery and 100% test compatibility.
"""

from pathlib import Path

MODULES = [
    "state.js",
    "components/theme.js",
    "formatters.js",
    "components/charts.js",
    "views/alerts.js",
    "views/overview.js",
    "views/topology.js",
    "views/bandwidth.js",
    "views/flows.js",
    "views/syslog.js",
    "views/timeline.js",
    "views/health.js",
    "components/palette.js",
    "components/route.js",
    "components/tv_mode.js",
    "components/controls.js",
    "components/scroll_hints.js",
    "views/settings.js",
    "views/welcome.js",
    "main.js",
]


def bundle_app_js():
    root = Path(__file__).resolve().parent.parent
    js_dir = root / "web" / "static" / "js"
    target = root / "web" / "static" / "app.js"

    chunks = [
        "// Mini-NOC Dashboard Client Bundle\n",
        "// Generated from web/static/js/ modules for fast, zero-CDN static delivery.\n\n",
    ]

    for rel_path in MODULES:
        source_file = js_dir / rel_path
        if not source_file.exists():
            raise FileNotFoundError(f"Missing JS module: {source_file}")
        content = source_file.read_text(encoding="utf-8")
        chunks.append(f"// --- BEGIN: {rel_path} ---\n")
        chunks.append(content.rstrip() + "\n")
        chunks.append(f"// --- END: {rel_path} ---\n\n")

    target.write_text("".join(chunks), encoding="utf-8")
    return len(chunks)


if __name__ == "__main__":
    bundle_app_js()
    print("Successfully bundled web/static/app.js")


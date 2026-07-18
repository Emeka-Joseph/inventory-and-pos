"""
Receipt Print Agent (Flask/Python version)

A tiny local service the POS web app talks to over http://127.0.0.1:PORT.
It exists for exactly one reason: to print receipts as raw ESC/POS bytes
through the OS print spooler's RAW datatype, instead of through the browser's
window.print() -> OS page-layout pipeline, which is what causes the
pagination/blank-gap bug on some printer drivers.

Setup for a shop owner is: install this once, open the setup page it opens
automatically, pick their printer from a dropdown, done. No paper size, no
roll-length, nothing driver-related to touch, ever again.
"""

import base64
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

import config
import printer_service
from escpos_image import build_raster_job

VERSION = "1.0.0"
PORT = int(os.environ.get("PRINT_AGENT_PORT", 19100))


def resource_path(relative_path: str) -> Path:
    """Resolve a path to a bundled resource (e.g. public/setup.html) that
    works both when running `python app.py` directly and when running as a
    PyInstaller-built .exe, which extracts data files to a temp folder
    referenced by sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).parent
    return base_path / relative_path


app = Flask(__name__, static_folder=str(resource_path("public")), static_url_path="")


@app.after_request
def add_cors_headers(response):
    # The POS web app is very likely served from a different origin (its own
    # https:// domain) than this agent (http://127.0.0.1), so every response
    # needs explicit CORS headers. `allowedOrigins` is read fresh from config
    # on every request so changing it via POST /config takes effect
    # immediately, no restart needed.
    origin = request.headers.get("Origin")
    allowed = config.get().get("allowedOrigins")

    if not allowed:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif isinstance(allowed, list) and origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    elif isinstance(allowed, str) and origin == allowed:
        response.headers["Access-Control-Allow-Origin"] = origin

    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    # Chrome's Private Network Access check: an https:// page fetching a
    # private address like 127.0.0.1 sends a preflight asking for explicit
    # permission. Without this header, the browser silently blocks the request.
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.route("/")
def index():
    # Explicit route rather than relying on Flask's static-folder handling to
    # auto-resolve "/" to an index file -- it doesn't, by default.
    return app.send_static_file("setup.html")


@app.route("/status")
def status():
    printers = printer_service.list_printers()
    cfg = config.get()
    return jsonify(
        {
            "status": "ok",
            "version": VERSION,
            "defaultPrinter": cfg.get("defaultPrinter"),
            "printers": printers,
        }
    )


@app.route("/printers")
def printers_route():
    return jsonify({"printers": printer_service.list_printers()})


@app.route("/config", methods=["POST"])
def config_route():
    body = request.get_json(silent=True) or {}
    partial = {}
    if "defaultPrinter" in body:
        partial["defaultPrinter"] = body["defaultPrinter"]
    if "allowedOrigins" in body:
        partial["allowedOrigins"] = body["allowedOrigins"]
    updated = config.update(partial)
    return jsonify({"ok": True, "config": updated})


@app.route("/print-image", methods=["POST"])
def print_image():
    body = request.get_json(silent=True) or {}
    width = body.get("width")
    height = body.get("height")
    bitmap_b64 = body.get("bitmapBase64")
    cut = body.get("cut", True)

    if not width or not height or not bitmap_b64:
        return jsonify({"ok": False, "error": "width, height and bitmapBase64 are required"}), 400

    try:
        bitmap = base64.b64decode(bitmap_b64)
        job = build_raster_job(width, height, bitmap, cut)
    except Exception as err:
        return jsonify({"ok": False, "error": str(err)}), 400

    target_printer = body.get("printerName") or config.get().get("defaultPrinter")
    if not target_printer:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"No printer configured yet. Open http://127.0.0.1:{PORT}/ and choose one.",
                }
            ),
            400,
        )

    try:
        printer_service.print_raw(job, target_printer)
    except Exception as err:
        return jsonify({"ok": False, "error": str(err)}), 500

    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"Receipt print agent v{VERSION} listening on http://127.0.0.1:{PORT}")
    print(f"First-time setup: open http://127.0.0.1:{PORT}/ and choose the default printer.")
    app.run(host="127.0.0.1", port=PORT)

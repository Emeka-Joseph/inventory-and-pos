# Receipt Print Agent (Python/Flask)

A small local service that prints POS receipts directly to a thermal printer as
a raw ESC/POS raster image, instead of going through the browser's
`window.print()` → OS page-layout pipeline. That pipeline is what causes the
blank-gap pagination bug you get with `window.print()` on many thermal printer
drivers: the driver silently negotiates its own fixed page length regardless
of your CSS `@page` size, and any content that doesn't fit gets pushed onto a
"next page" that a continuous roll printer just prints right after a stretch
of blank feed.

This agent sidesteps that entirely. It never asks the OS to lay out a page —
it sends a **RAW** print job (the OS's own mechanism for "just send these
exact bytes to the printer, no interpretation"), containing a bitmap of the
receipt exactly as tall as the receipt actually is. There is no page for any
driver to mis-size.

## How it fits together

1. The POS web page renders the receipt as normal HTML (unchanged — same
   template, same styling, any language/script/logo).
2. `app/static/js/print-client.js` (loaded by `receipt.html`) rasterizes that
   HTML to a canvas with `html2canvas`, converts it to a 1-bit monochrome
   bitmap, and POSTs it to this agent at `http://127.0.0.1:19100/print-image`.
3. This agent wraps that bitmap in the ESC/POS raster-image command
   (`escpos_image.py`) and sends it to the printer as a RAW job via the OS
   print spooler (`printer_service.py`, using `pywin32`'s `win32print` on
   Windows, or CUPS's `lp -o raw` on Mac/Linux).
4. The printer prints exactly that many dot-rows, then cuts. No pagination.

If the agent isn't running (not installed yet, or the till hasn't started it),
`print-client.js` automatically falls back to the existing `window.print()`
flow, so the app keeps working while a shop transitions.

## Running it (development)

```bash
cd print-agent
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:19100/` in a browser on that machine and pick the
default printer from the dropdown. That's the entire setup — no paper size,
no roll length, nothing driver-related.

## Packaging for shop owners (what actually gets distributed)

Shop owners should never install Python or run `pip install` themselves. You
build one self-contained `.exe` (this bundles Python + your code, built once
by you) and hand them a file to double-click:

```bash
pip install pyinstaller
pyinstaller --onefile --name receipt-print-agent --add-data "public;public" app.py
```

(On Mac/Linux, `--add-data` uses `:` instead of `;`: `"public:public"`.)

The built file lands in `dist/receipt-print-agent.exe` (or the platform
equivalent). `app.py`'s `resource_path()` helper is what makes `public/setup.html`
resolve correctly once bundled — PyInstaller extracts data files to a temp
folder at runtime (`sys._MEIPASS`), which is different from the source layout,
so don't remove that helper.

Build the Windows executable **on a Windows machine** — `pywin32` is a native
Windows API wrapper, so it needs to be compiled/available for the OS you're
targeting; there's no meaningful cross-compilation path here. If you don't
have a spare Windows PC, a free Windows VM (or a GitHub Actions `windows-latest`
runner) works fine for this.

### Turning the .exe into a real installer (recommended before shipping)

A loose `.exe` works, but for true plug-and-play you want it to launch
automatically on boot, without a cashier ever remembering to open it. Wrap the
PyInstaller output in a proper installer using
[Inno Setup](https://jrsoftware.org/isinfo.php) (free, Windows-native, simple
script format) — have it:
- copy `receipt-print-agent.exe` somewhere permanent (e.g. `Program Files`),
- add a shortcut to the user's Startup folder (`{userstartup}` in Inno Setup),
  so it runs quietly every time the till boots,
- optionally launch `http://127.0.0.1:19100/` in the default browser right
  after install, so the printer-selection step happens immediately.

That installer is the one file you actually hand to shop owners.

### A note on SmartScreen warnings
An unsigned `.exe`/installer will trigger a "Windows protected your PC"
warning the first time it's run — alarming for a non-technical user. A
code-signing certificate (from any standard CA, renewed yearly) removes this.
Not required for an MVP, but worth budgeting for once you're distributing this
broadly.

## Endpoints

| Method | Path           | Purpose                                             |
|--------|----------------|------------------------------------------------------|
| GET    | `/`            | Setup page — pick the default printer |
| GET    | `/status`      | Health check + list of installed printers + configured default |
| GET    | `/printers`    | Just the printer list |
| POST   | `/config`      | `{ defaultPrinter, allowedOrigins }` — persisted to `~/.receipt-print-agent/config.json` |
| POST   | `/print-image` | `{ width, height, bitmapBase64, printerName?, cut? }` — prints it |

This is the exact same contract as the earlier Node.js version, so nothing on
the `app/static/js/print-client.js` side needs to change.

## Known caveats / things to verify on real hardware

- **Cut command**: `escpos_image.py` sends `GS V 66 0` (feed + full cut),
  the common Epson-compatible sequence. Some cheap ESC/POS clones only
  support partial cut — if the receipt prints correctly but never cuts, swap
  in the alternate line noted in that file (`GS V 1`).
- **Raster chunk size**: images are sent in 200-dot-tall strips
  (`MAX_CHUNK_ROWS` in `escpos_image.py`) to avoid overrunning small printer
  buffers on very long receipts. Lower this if you see corrupted/partial
  output on long receipts with a particular printer model.
- **CORS / Private Network Access**: the POS app's origin must be allowed —
  set `allowedOrigins` via `POST /config` once you're not just testing on
  `*`. Chrome also requires the `Access-Control-Allow-Private-Network`
  header for an https:// page to reach `127.0.0.1`; `app.py` already sets it.
- **Mac/Linux printer queues**: make sure the printer's CUPS queue is set up
  as a raw/generic queue (not a PostScript-interpreting one) — otherwise CUPS
  will try to reinterpret the raw bytes instead of passing them straight
  through.
- **Multiple printers per till**: pass `printerName` in the `/print-image`
  body to target a specific printer without changing the saved default
  (useful for kitchen-copy vs. customer-copy setups later).

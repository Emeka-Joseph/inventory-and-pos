"""
Thin wrapper around the OS print spooler: pywin32's win32print on Windows,
CUPS (via the `lp`/`lpstat` command-line tools) on Mac/Linux.

The key detail: every job is sent RAW. A RAW job is handed to the printer
as-is, with no page layout, no pagination, no "fit to paper size" logic
applied by the OS -- exactly what we want, since we've already rendered the
receipt to an exact-height bitmap ourselves in escpos_image.py. This works
with whatever driver is already installed for the printer (the Xprinter
vendor driver, or Windows' generic "Text Only" driver) -- nothing
printer-driver-specific needs to be reconfigured.
"""

import platform
import subprocess

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    import win32print


def list_printers() -> list[str]:
    if IS_WINDOWS:
        try:
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in win32print.EnumPrinters(flags)]
        except Exception as err:
            print(f"Failed to enumerate printers: {err}")
            return []
    else:
        try:
            result = subprocess.run(
                ["lpstat", "-p"], capture_output=True, text=True, timeout=5
            )
            return [
                line.split()[1]
                for line in result.stdout.splitlines()
                if line.startswith("printer ")
            ]
        except Exception as err:
            print(f"Failed to enumerate printers: {err}")
            return []


def get_default_printer():
    if IS_WINDOWS:
        try:
            return win32print.GetDefaultPrinter()
        except Exception:
            return None
    else:
        try:
            result = subprocess.run(
                ["lpstat", "-d"], capture_output=True, text=True, timeout=5
            )
            line = result.stdout.strip()
            if ":" in line:
                return line.split(":", 1)[1].strip()
        except Exception:
            return None
    return None


def print_raw(data: bytes, printer_name: str):
    """Send `data` straight to `printer_name` with no OS page layout applied.
    Raises on failure; caller is expected to catch and report it."""
    if IS_WINDOWS:
        h_printer = win32print.OpenPrinter(printer_name)
        try:
            h_job = win32print.StartDocPrinter(h_printer, 1, ("Receipt", None, "RAW"))
            try:
                win32print.StartPagePrinter(h_printer)
                win32print.WritePrinter(h_printer, data)
                win32print.EndPagePrinter(h_printer)
            finally:
                win32print.EndDocPrinter(h_printer)
        finally:
            win32print.ClosePrinter(h_printer)
    else:
        # -o raw tells CUPS to pass the bytes straight through instead of
        # trying to reinterpret them as PostScript/text -- make sure the
        # printer's CUPS queue itself is also set up as a raw/generic queue.
        proc = subprocess.run(
            ["lp", "-d", printer_name, "-o", "raw"], input=data, capture_output=True
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace"))

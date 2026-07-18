"""
Builds a raw ESC/POS byte stream that prints a pre-rendered 1-bit-per-pixel
monochrome bitmap as a raster image, then feeds and cuts the paper.

This is the piece that actually eliminates the whitespace/pagination bug:
there is no "page" concept here at all, just a byte stream describing exactly
as many dot-rows as the receipt is tall. The printer prints every row it's
given and then cuts -- it can't insert a page break because we never declared
a page in the first place.

The bitmap itself is expected to already be packed MSB-first, one bit per
pixel, ceil(width / 8) bytes per row -- see web-client/print-client.js, which
renders the receipt DOM to a canvas and packs it client-side.
"""

ESC = 0x1B
GS = 0x1D

# Cheap thermal printer controllers (including many Xprinter/clone boards) can
# choke on one giant raster command for a very long receipt, so we send the
# image in horizontal strips instead of a single block.
MAX_CHUNK_ROWS = 200


def build_raster_job(width: int, height: int, bitmap: bytes, cut: bool = True) -> bytes:
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")

    bytes_per_row = (width + 7) // 8
    expected_length = bytes_per_row * height
    if len(bitmap) != expected_length:
        raise ValueError(
            f"Bitmap size mismatch: expected {expected_length} bytes for "
            f"{width}x{height} ({bytes_per_row} bytes/row), got {len(bitmap)}"
        )

    parts = [bytes([ESC, 0x40])]  # ESC @ : initialize printer, clear any prior state

    row_start = 0
    while row_start < height:
        rows = min(MAX_CHUNK_ROWS, height - row_start)
        slice_start = row_start * bytes_per_row
        slice_end = slice_start + rows * bytes_per_row
        chunk = bitmap[slice_start:slice_end]

        # GS v 0 m xL xH yL yH d1...dk -- print raster bit image, m=0 (normal size)
        x_l = bytes_per_row & 0xFF
        x_h = (bytes_per_row >> 8) & 0xFF
        y_l = rows & 0xFF
        y_h = (rows >> 8) & 0xFF

        parts.append(bytes([GS, 0x76, 0x30, 0x00, x_l, x_h, y_l, y_h]))
        parts.append(chunk)
        row_start += rows

    if cut:
        parts.append(bytes([0x0A, 0x0A, 0x0A]))  # feed a few lines so the cutter clears the last text row
        parts.append(bytes([GS, 0x56, 0x42, 0x00]))  # GS V 66 0 : feed + full cut
        # NOTE: some cheaper ESC/POS clones only support partial cut. If receipts
        # print fine but never cut, try replacing the line above with:
        #   parts.append(bytes([GS, 0x56, 0x01]))  # GS V 1 : partial cut

    return b"".join(parts)

/**
 * print-client.js
 *
 * Talks to the locally-installed Receipt Print Agent (see /print-agent) to print
 * thermal receipts as a raw ESC/POS raster image. This is what avoids the
 * browser's window.print() page-layout pipeline entirely -- since the receipt
 * is rasterized to an image of its exact rendered height and sent as a single
 * byte stream, there is no "page size" for any driver to mis-negotiate, and no
 * more blank-gap pagination.
 *
 * It also means whatever renders correctly on screen -- any language, script,
 * logo, custom font -- prints identically, since the browser itself is doing
 * the text rendering, not a limited printer codepage.
 *
 * Falls back automatically to the existing window.print() flow if the agent
 * isn't installed/running on this machine, so the app keeps working either way
 * while a shop is transitioning to the agent.
 *
 * Requires html2canvas to be loaded on the page first (vendored at
 * app/static/js/html2canvas.min.js).
 */

(function (global) {
  const AGENT_URL = 'http://127.0.0.1:19100';
  const AGENT_PING_TIMEOUT_MS = 800;
  const MONOCHROME_THRESHOLD = 200; // luminance below this = printed (black) dot

  async function isAgentAvailable() {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), AGENT_PING_TIMEOUT_MS);
      const res = await fetch(`${AGENT_URL}/status`, { signal: controller.signal });
      clearTimeout(timer);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null; // agent not installed, not running, or blocked -- caller should fall back
    }
  }

  function dotsWidthForPaper(paperWidthMm) {
    // Thermal rolls print at roughly 203dpi; 80mm heads are commonly 576 dots
    // wide, 58mm heads 384 dots. Override via opts.dotsWidth if your printer differs.
    return paperWidthMm === 58 ? 384 : 576;
  }

  // Renders the receipt DOM node to a canvas at the printer's exact dot-width,
  // then packs it into a 1-bit-per-pixel monochrome bitmap (MSB-first per row)
  // -- exactly the format escposImage.js on the agent side expects, so the
  // agent does no image decoding at all, just framing + sending raw bytes.
  async function renderElementToMonochromeBitmap(element, dotsWidth) {
    if (typeof html2canvas !== 'function') {
      throw new Error('html2canvas is required on the page for agent-based printing.');
    }

    const sourceCanvas = await html2canvas(element, {
      backgroundColor: '#ffffff',
      scale: dotsWidth / element.offsetWidth,
      windowWidth: element.offsetWidth,
    });

    // Re-draw onto a canvas of the exact target pixel width, in case the
    // scale factor above didn't land on an exact integer width.
    const target = document.createElement('canvas');
    target.width = dotsWidth;
    target.height = Math.round(sourceCanvas.height * (dotsWidth / sourceCanvas.width));
    const ctx = target.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, target.width, target.height);
    ctx.drawImage(sourceCanvas, 0, 0, target.width, target.height);

    const { data } = ctx.getImageData(0, 0, target.width, target.height);
    const bytesPerRow = Math.ceil(target.width / 8);
    const packed = new Uint8Array(bytesPerRow * target.height);

    for (let y = 0; y < target.height; y++) {
      for (let x = 0; x < target.width; x++) {
        const i = (y * target.width + x) * 4;
        const luminance = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        if (luminance < MONOCHROME_THRESHOLD) {
          const byteIndex = y * bytesPerRow + (x >> 3);
          packed[byteIndex] |= 0x80 >> (x & 7);
        }
      }
    }

    let binary = '';
    for (let i = 0; i < packed.length; i++) binary += String.fromCharCode(packed[i]);

    return {
      width: target.width,
      height: target.height,
      bitmapBase64: btoa(binary),
    };
  }

  /**
   * Main entry point. Call this instead of window.print() for the receipt.
   *
   * @param {Object} opts
   * @param {string} [opts.elementId='receiptContent']
   * @param {number} [opts.paperWidthMm=80]
   * @param {number} [opts.dotsWidth] override the computed printer dot-width
   * @param {string} [opts.printerName] override the agent's configured default printer
   * @param {Function} [opts.onFallback] called with a reason string if we fall back to window.print()
   */
  async function printReceipt(opts = {}) {
    const {
      elementId = 'receiptContent',
      paperWidthMm = 80,
      dotsWidth,
      printerName,
      onFallback,
    } = opts;

    const status = await isAgentAvailable();
    const element = document.getElementById(elementId);

    if (!status) {
      if (onFallback) onFallback('agent-unavailable');
      window.print();
      return;
    }
    if (!element) {
      if (onFallback) onFallback('missing-element');
      window.print();
      return;
    }

    try {
      const width = dotsWidth || dotsWidthForPaper(paperWidthMm);
      const bitmap = await renderElementToMonochromeBitmap(element, width);
      const res = await fetch(`${AGENT_URL}/print-image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...bitmap, printerName, cut: true }),
      });
      const result = await res.json();
      if (!result.ok) throw new Error(result.error || 'Agent reported a print failure');
    } catch (err) {
      console.error('Agent print failed, falling back to browser print:', err);
      if (onFallback) onFallback('agent-error', err);
      window.print();
    }
  }

  global.ReceiptPrinter = { printReceipt, isAgentAvailable };
})(window);

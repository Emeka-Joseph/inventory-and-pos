// Direct thermal-printer support via QZ Tray (https://qz.io).
//
// Printing goes through qz.print({type:'raw', format:'html', options:{language:'ESCPOS'}}):
// QZ Tray rasterizes the given HTML itself and streams the result as ESC/POS bytes straight
// to the printer, with no OS page/paper-size negotiation involved — this is what actually
// avoids the page-break/blank-gap bug that browser window.print() has on real printer drivers.
// (qz.print's 'pixel' mode still goes through the OS print pipeline, so it would NOT fix this.)

let _qzSecurityConfigured = false;

function _ensureQzSecurityConfigured() {
  if (_qzSecurityConfigured) return;
  qz.security.setCertificatePromise(function(resolve, reject) {
    fetch('/qz/certificate').then(r => r.text()).then(resolve, reject);
  });
  qz.security.setSignatureAlgorithm('SHA512');
  qz.security.setSignaturePromise(function(toSign) {
    return function(resolve, reject) {
      fetch('/qz/sign-message?request=' + encodeURIComponent(toSign))
        .then(r => r.text()).then(resolve, reject);
    };
  });
  _qzSecurityConfigured = true;
}

async function _ensureQzConnected() {
  _ensureQzSecurityConfigured();
  if (!qz.websocket.isActive()) {
    await qz.websocket.connect();
  }
}

// Fetches the no-chrome receipt HTML (with our own session cookie) and hands it to QZ Tray
// as a plain HTML string — QZ never makes its own HTTP request, so there's no auth/cookie
// problem to work around.
async function printReceiptViaQz(receiptPrintUrl, printerName, paperWidthMm) {
  if (!printerName) throw new Error('No printer configured for direct printing.');
  await _ensureQzConnected();

  const res = await fetch(receiptPrintUrl, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('Could not load receipt content to print.');
  const html = await res.text();

  const config = qz.configs.create(printerName, {
    units: 'mm',
    size: { width: paperWidthMm, custom: true },
  });
  const data = [{
    type: 'raw',
    format: 'html',
    flavor: 'plain',
    data: html,
    options: { language: 'ESCPOS' },
  }];
  await qz.print(config, data);
}

// Used by the "Detect Printers" button in Business Settings.
async function detectPrinters() {
  await _ensureQzConnected();
  return qz.printers.find();
}

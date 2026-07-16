import base64

from flask import Blueprint, current_app, request
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

qz_bp = Blueprint('qz', __name__, url_prefix='/qz')


@qz_bp.route('/certificate')
def certificate():
    """Public certificate QZ Tray uses to verify our signed print requests."""
    cert = current_app.config.get('QZ_CERTIFICATE', '')
    return cert, 200, {'Content-Type': 'text/plain'}


@qz_bp.route('/sign-message')
def sign_message():
    """
    Signs the opaque nonce QZ Tray sends before trusting a print request.
    This never touches business data -- it only proves requests came from this
    server, matching the certificate served at /qz/certificate.
    """
    to_sign = request.args.get('request', '')
    pem = current_app.config.get('QZ_PRIVATE_KEY', '')
    private_key = serialization.load_pem_private_key(pem.encode(), password=None)
    signature = private_key.sign(
        to_sign.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA512(),
    )
    return base64.b64encode(signature).decode(), 200, {'Content-Type': 'text/plain'}

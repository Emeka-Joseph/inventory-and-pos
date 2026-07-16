"""
Run this script ONCE to generate the self-signed RSA keypair QZ Tray uses to verify that
print requests really came from this app (see app/routes/qz.py). Paste the printed output
into your .env (local) or your cPanel Python App's environment variables (production).

Usage: python scripts/generate_qz_cert.py
"""
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "Eventry POS QZ Tray Signing"),
])
certificate = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(private_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(timezone.utc))
    .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
    .sign(private_key, hashes.SHA256())
)

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()


def as_env_line(name, pem):
    escaped = pem.replace("\n", "\\n")
    return f"{name}={escaped}"


print("Paste these two lines into your .env (keep them on one line each):\n")
print(as_env_line("QZ_CERTIFICATE", cert_pem))
print(as_env_line("QZ_PRIVATE_KEY", private_pem))
print("\nThis certificate is self-signed and valid for 10 years. The first time a business's")
print("QZ Tray connects, it will show a one-time 'trust this site?' prompt — that's expected")
print("for self-signed certs (a paid QZ certificate would remove it, but costs money).")

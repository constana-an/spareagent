"""Generate a throwaway development CA + server certificate for the TLS API listener.

    python scripts/dev_certs.py /certs

Writes ca.pem, server.pem, server-key.pem (SAN: api-tls, localhost, 127.0.0.1). Idempotent:
existing certificates are kept unless they expire within a day. Development only.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SANS = ["api-tls", "localhost"]


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _valid(path: Path) -> bool:
    if not path.exists():
        return False
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    return cert.not_valid_after_utc > dt.datetime.now(dt.UTC) + dt.timedelta(days=1)


def main(out: str) -> int:
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    if _valid(root / "server.pem") and _valid(root / "ca.pem"):
        print(f"dev certificates already present in {root}")
        return 0
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("SpareAgent Dev CA"))
        .issuer_name(_name("SpareAgent Dev CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(n) for n in SANS] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("api-tls"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = root / "server-key.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    print(f"wrote dev CA and server certificate to {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/certs"))

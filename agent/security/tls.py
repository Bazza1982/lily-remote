"""TLS certificate management for Lily Remote Agent."""

import ipaddress
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Tuple

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def get_cert_dir() -> Path:
    """Get the certificate storage directory."""
    cert_dir = Path.home() / ".lily-remote" / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    return cert_dir


def generate_self_signed_cert(
    hostname: str = "localhost",
    valid_days: int = 365,
) -> Tuple[Path, Path]:
    """
    Generate a self-signed TLS certificate and private key.

    Args:
        hostname: The hostname for the certificate CN and SAN.
        valid_days: Certificate validity period in days.

    Returns:
        Tuple of (cert_path, key_path).
    """
    cert_dir = get_cert_dir()
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"

    # Generate RSA private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Build certificate subject
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Lily Remote"),
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])

    # Build certificate
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(hostname),
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            ]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    # Write private key
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)

    # Write certificate
    cert_path.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    return cert_path, key_path


def load_or_generate_cert(hostname: str = "localhost") -> Tuple[Path, Path]:
    """
    Load existing certificate or generate a new one if not present.

    Args:
        hostname: The hostname for the certificate.

    Returns:
        Tuple of (cert_path, key_path).
    """
    cert_dir = get_cert_dir()
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"

    if cert_path.exists() and key_path.exists():
        # Verify certificate is not expired
        cert_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_data)

        if cert.not_valid_after_utc > datetime.now(timezone.utc):
            return cert_path, key_path

    # Generate new certificate
    return generate_self_signed_cert(hostname)


def get_cert_fingerprint(cert_path: Path) -> str:
    """
    Get the SHA256 fingerprint of a certificate.

    Args:
        cert_path: Path to the certificate file.

    Returns:
        Hex-encoded SHA256 fingerprint.
    """
    cert_data = cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_data)
    fingerprint = cert.fingerprint(hashes.SHA256())
    return fingerprint.hex().upper()

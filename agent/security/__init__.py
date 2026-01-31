"""Security module for Lily Remote Agent."""

from .tls import (
    get_cert_dir,
    generate_self_signed_cert,
    load_or_generate_cert,
    get_cert_fingerprint,
)
from .pairing import (
    PairingState,
    PairingRequest,
    PairedClient,
    PairingManager,
)
from .auth import (
    set_pairing_manager,
    get_pairing_manager,
    verify_token,
    optional_verify_token,
)

__all__ = [
    "get_cert_dir",
    "generate_self_signed_cert",
    "load_or_generate_cert",
    "get_cert_fingerprint",
    "PairingState",
    "PairingRequest",
    "PairedClient",
    "PairingManager",
    "set_pairing_manager",
    "get_pairing_manager",
    "verify_token",
    "optional_verify_token",
]

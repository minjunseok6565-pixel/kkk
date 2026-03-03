from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ContractNegotiationError(Exception):
    """Structured error for contract negotiation flows.

    The server layer can map these to HTTP 4xx/5xx while keeping a stable
    machine-readable code for client/UI.
    """

    code: str
    message: str
    details: Optional[Any] = None

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


# Error codes (stable API surface)
NEGOTIATION_NOT_FOUND = "NEGOTIATION_NOT_FOUND"
NEGOTIATION_BAD_PAYLOAD = "NEGOTIATION_BAD_PAYLOAD"
NEGOTIATION_INVALID_OFFER = "NEGOTIATION_INVALID_OFFER"
NEGOTIATION_INVALID_MODE = "NEGOTIATION_INVALID_MODE"
NEGOTIATION_EXPIRED = "NEGOTIATION_EXPIRED"
NEGOTIATION_CLOSED = "NEGOTIATION_CLOSED"
NEGOTIATION_COMMIT_NOT_ACCEPTED = "NEGOTIATION_COMMIT_NOT_ACCEPTED"
NEGOTIATION_COMMIT_FAILED = "NEGOTIATION_COMMIT_FAILED"

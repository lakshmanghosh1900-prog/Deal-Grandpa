from __future__ import annotations

from typing import Any

import rfc8785


def canonical_bytes(payload: Any) -> bytes:
    """Return RFC 8785 JSON Canonicalization Scheme bytes."""
    return rfc8785.dumps(payload)


def canonical_hash(payload: Any) -> str:
    import hashlib
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()

"""Tiny helpers for dict merging and SHA-256 hashing."""

import hashlib
from typing import Any

from loguru import logger


def merge_dicts_keep_first(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """Copy *secondary* and back-fill missing keys with non-empty values from *primary*."""
    out: dict[str, Any] = dict(secondary)
    for k, v in primary.items():
        if v:
            out.setdefault(k, v)
    logger.debug("dicts_merged", primary=len(primary), secondary=len(secondary), result=len(out))
    return out


def sha256(text: str) -> str:
    """Return lower-case SHA-256 hex digest of *text*."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    logger.debug("sha256", length=len(text))
    return digest

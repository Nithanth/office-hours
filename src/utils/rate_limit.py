"""Centralised rate-limit helper using slowapi.

Import `limiter` and decorators from this module to apply per-route limits.
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Global limiter instance with sensible defaults.
# Override/extend per-route limits via the decorator.
limiter: Limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])  # ~1.6 r/s

# Re-export helpers for convenience
__all__ = [
    "limiter",
    "RateLimitExceeded",
    "_rate_limit_exceeded_handler",
]

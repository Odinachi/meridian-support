"""Safe auth audit fields (no PINs, no full email addresses in logs)."""

from __future__ import annotations


def email_domain_only(email: str) -> str:
    """Return domain part for logging, or a sentinel if malformed."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return "invalid"
    _local, domain = e.rsplit("@", 1)
    return domain[:120] if domain else "invalid"

"""Detect logout requests and yes/no confirmations in support chat (pure helpers)."""

from __future__ import annotations

import re


def looks_like_logout_request(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return bool(
        re.fullmatch(r"(?i)(logout|log\s*out|sign\s*out)", t)
        or re.fullmatch(r"(?i)i\s+want\s+to\s+(log\s*out|logout|sign\s*out)\.?", t)
    )


def parse_logout_confirmation(text: str) -> str | None:
    """Return 'yes', 'no', or None if unclear."""
    t = text.strip()
    if not t:
        return None
    if re.fullmatch(
        r"(?i)(yes|y|yeah|yep|confirm|ok|okay|sure|please|sign\s*me\s*out|do\s*it)", t
    ):
        return "yes"
    if re.fullmatch(r"(?i)(no|n|nope|nah|cancel|stop|stay|never\s*mind|forget\s*it)", t):
        return "no"
    return None

"""Authenticated customer record (no secrets) — session helpers."""

from __future__ import annotations

from meridian.models import CustomerPrincipal


def principal_to_session_dict(p: CustomerPrincipal) -> dict[str, str]:
    return p.model_dump()


def principal_from_session_dict(data: dict) -> CustomerPrincipal:
    return CustomerPrincipal.model_validate(data)


__all__ = ["CustomerPrincipal", "principal_from_session_dict", "principal_to_session_dict"]

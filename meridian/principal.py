"""Authenticated customer record (no secrets)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerPrincipal:
    customer_id: str
    email: str
    display_name: str
    details_text: str


def principal_to_session_dict(p: CustomerPrincipal) -> dict:
    return {
        "customer_id": p.customer_id,
        "email": p.email,
        "display_name": p.display_name,
        "details_text": p.details_text,
    }


def principal_from_session_dict(data: dict) -> CustomerPrincipal:
    return CustomerPrincipal(
        customer_id=data["customer_id"],
        email=data["email"],
        display_name=data["display_name"],
        details_text=data.get("details_text", ""),
    )

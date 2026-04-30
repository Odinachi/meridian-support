"""Pydantic domain models — principals, auth context, order payloads."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from meridian.tools.get_product import GetProductValidationError, sanitize_sku

_UNIT_PRICE_RE = re.compile(r"^\d+(\.\d{1,4})?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class CustomerPrincipal(BaseModel):
    """Authenticated customer record (no secrets)."""

    model_config = ConfigDict(frozen=True)

    customer_id: str
    email: str
    display_name: str
    details_text: str = ""


class MeridianAuthContext(BaseModel):
    """Mutable run context shared with auth tools (one instance per Streamlit session)."""

    model_config = ConfigDict(validate_assignment=False)

    pending_principal: CustomerPrincipal | None = None
    last_tool_user_visible: str | None = Field(default=None, repr=False)


class OrderLineItem(BaseModel):
    """One catalog line on a create-order request (after normalization)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sku: str
    quantity: Annotated[int, Field(ge=1, le=9999)]
    unit_price: str
    currency: str = "USD"

    @field_validator("sku", mode="before")
    @classmethod
    def _validate_sku(cls, v: Any) -> str:
        try:
            return sanitize_sku(str(v or ""))
        except GetProductValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("quantity", mode="before")
    @classmethod
    def _reject_bool_and_coerce(cls, v: Any) -> int:
        if isinstance(v, bool):
            raise ValueError("Each line needs a numeric quantity > 0.")
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v == int(v):
            return int(v)
        if isinstance(v, float):
            raise ValueError("Quantity must be a whole number.")
        raise ValueError("Quantity must be a whole number.")

    @field_validator("unit_price", mode="before")
    @classmethod
    def _normalize_unit_price(cls, v: Any) -> str:
        if not isinstance(v, (str, int, float)):
            raise ValueError("unit_price must be a number or decimal string.")
        s = str(v).strip()
        if len(s) > 24:
            raise ValueError("unit_price is too long.")
        if not _UNIT_PRICE_RE.match(s):
            raise ValueError(
                "unit_price must look like a positive decimal (e.g. 99.99 or 100)."
            )
        return s

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "USD"
        if not isinstance(v, str):
            raise ValueError("currency must be a 3-letter code or omitted.")
        c = v.strip().upper()
        if not _CURRENCY_RE.match(c):
            raise ValueError("currency must be a 3-letter ISO-style code (e.g. USD).")
        return c

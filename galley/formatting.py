"""Number formatting for tables and text: the one place conventions are set."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from galley.config import PaperConfig

Number = int | float


@dataclass(frozen=True)
class NumberStyle:
    thousands: str = ","
    decimal: str = "."
    negative: Literal["minus", "parentheses"] = "minus"

    @classmethod
    def from_config(cls, config: PaperConfig) -> NumberStyle:
        raw = config.numbers
        negative = str(raw.get("negative", "minus"))
        if negative not in ("minus", "parentheses"):
            raise ValueError(f"numbers.negative must be minus or parentheses, got '{negative}'")
        return cls(
            thousands=str(raw.get("thousands", ",")),
            decimal=str(raw.get("decimal", ".")),
            negative="parentheses" if negative == "parentheses" else "minus",
        )


DEFAULT_STYLE = NumberStyle()


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _magnitude(value: Number, decimals: int, style: NumberStyle) -> str:
    text = f"{abs(value):,.{decimals}f}"
    whole, _, fraction = text.partition(".")
    whole = whole.replace(",", style.thousands)
    return whole + (style.decimal + fraction if fraction else "")


def _signed(value: Number, body: str, decimals: int, style: NumberStyle) -> str:
    # A value that rounds to zero is shown without a sign.
    if value >= 0 or round(abs(value), decimals) == 0:
        return body
    return f"({body})" if style.negative == "parentheses" else f"-{body}"


def fmt_number(value: Number | None, decimals: int = 0, style: NumberStyle = DEFAULT_STYLE) -> str:
    """``12345.6`` -> ``12,346``."""
    if is_missing(value) or value is None:
        return ""
    return _signed(value, _magnitude(value, decimals, style), decimals, style)


def fmt_percent(
    value: Number | None,
    decimals: int = 1,
    style: NumberStyle = DEFAULT_STYLE,
    *,
    ratio: bool = True,
) -> str:
    """``0.125`` -> ``12.5%``. Pass ``ratio=False`` if the value is already in percent."""
    if is_missing(value) or value is None:
        return ""
    scaled = value * 100 if ratio else value
    return _signed(scaled, _magnitude(scaled, decimals, style) + "%", decimals, style)


def fmt_bp(value: Number | None, decimals: int = 0, style: NumberStyle = DEFAULT_STYLE) -> str:
    """``25`` -> ``25bp``. The value is in basis points."""
    if is_missing(value) or value is None:
        return ""
    return _signed(value, _magnitude(value, decimals, style) + "bp", decimals, style)


def fmt_currency(
    value: Number | None,
    decimals: int = 0,
    style: NumberStyle = DEFAULT_STYLE,
    *,
    symbol: str = "$",
    suffix: str = "",
) -> str:
    """``1234.5`` -> ``$1,235``; ``suffix="m"`` gives ``$1,235m``."""
    if is_missing(value) or value is None:
        return ""
    return _signed(value, symbol + _magnitude(value, decimals, style) + suffix, decimals, style)


Formatter = Callable[[Any], str]


def named_formatter(spec: str, style: NumberStyle = DEFAULT_STYLE) -> Formatter:
    """Resolve ``"number"``, ``"number:2"``, ``"percent:1"``, ``"bp"``, ``"currency:0"``."""
    name, _, arg = spec.partition(":")
    functions: dict[str, tuple[Callable[..., str], int]] = {
        "number": (fmt_number, 0),
        "percent": (fmt_percent, 1),
        "bp": (fmt_bp, 0),
        "currency": (fmt_currency, 0),
    }
    if name not in functions:
        raise ValueError(f"unknown number format '{spec}'")
    function, default_decimals = functions[name]
    decimals = int(arg) if arg else default_decimals
    return lambda value: function(value, decimals, style)


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    return "".join(_LATEX_SPECIALS.get(char, char) for char in text)

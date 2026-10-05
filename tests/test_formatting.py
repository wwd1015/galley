from __future__ import annotations

import math

import pytest

from galley.config import PaperConfig
from galley.formatting import (
    NumberStyle,
    fmt_bp,
    fmt_currency,
    fmt_number,
    fmt_percent,
    latex_escape,
    named_formatter,
)

PARENS = NumberStyle(negative="parentheses")
EUROPEAN = NumberStyle(thousands=".", decimal=",")


def test_number_thousands_and_rounding() -> None:
    assert fmt_number(12345.6) == "12,346"
    assert fmt_number(12345.678, 2) == "12,345.68"
    assert fmt_number(0) == "0"


def test_number_negative_styles() -> None:
    assert fmt_number(-1234) == "-1,234"
    assert fmt_number(-1234, style=PARENS) == "(1,234)"
    assert fmt_number(-0.004, 2) == "0.00"


def test_number_separators_from_style() -> None:
    assert fmt_number(1234567.5, 1, EUROPEAN) == "1.234.567,5"


def test_percent() -> None:
    assert fmt_percent(0.125) == "12.5%"
    assert fmt_percent(12.5, ratio=False) == "12.5%"
    assert fmt_percent(-0.05, 0, PARENS) == "(5%)"


def test_bp_and_currency() -> None:
    assert fmt_bp(25) == "25bp"
    assert fmt_bp(-12.5, 1) == "-12.5bp"
    assert fmt_currency(1234.5) == "$1,234"
    assert fmt_currency(2275, suffix="m") == "$2,275m"
    assert fmt_currency(-3, style=PARENS, symbol="€") == "(€3)"


@pytest.mark.parametrize("missing", [None, math.nan])
def test_missing_values_are_blank(missing: float | None) -> None:
    assert fmt_number(missing) == ""
    assert fmt_percent(missing) == ""
    assert fmt_bp(missing) == ""
    assert fmt_currency(missing) == ""


def test_named_formatter() -> None:
    assert named_formatter("percent:0")(0.4) == "40%"
    assert named_formatter("number:2")(3) == "3.00"
    assert named_formatter("bp")(15) == "15bp"
    with pytest.raises(ValueError, match="unknown number format"):
        named_formatter("ratio")


def test_style_from_config() -> None:
    config = PaperConfig(numbers={"thousands": " ", "negative": "parentheses"})
    assert NumberStyle.from_config(config) == NumberStyle(thousands=" ", negative="parentheses")
    with pytest.raises(ValueError, match=r"numbers\.negative"):
        NumberStyle.from_config(PaperConfig(numbers={"negative": "red"}))


def test_latex_escape() -> None:
    assert latex_escape("5% of $m & R_d #1") == r"5\% of \$m \& R\_d \#1"

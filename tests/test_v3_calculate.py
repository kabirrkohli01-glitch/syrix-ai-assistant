"""Arithmetic that is evaluated, not predicted.

The tool exists because of one measured failure: asked for 12,500 at 4.2%
compounded monthly over seven years, SYRIX produced the right formula, the
right monthly rate and the right period count, then evaluated (1.0035)**84 as
1.32796 and answered £16,599.50. The true figure is £16,763.69. Two different
lanes made the same error, so the tests that matter are the exact-value ones
and the ones proving nothing but arithmetic can get through.
"""
from __future__ import annotations

import math

import pytest

from syrix_v3.tools import get as get_tool
from syrix_v3.tools.calculate import one


def test_the_compound_interest_case_that_was_wrong():
    assert "16,763.69" in one("12500 * (1 + 0.042/12)**84")


def test_the_power_that_was_mispredicted():
    """1.32796 was the answer given; 1.3410953... is the answer."""
    line = one("1.0035**84")
    assert line.startswith("1.0035**84 = 1.34109")
    assert "1.32796" not in line


@pytest.mark.parametrize("expression,expected", [
    ("2**10", "1,024"),
    ("16763.69 - 12500", "4,263.69"),
    ("factorial(10)", "3,628,800"),
    ("100 * 0.175", "17.5"),
    # A caret means exponentiation everywhere except Python. Treating it as
    # bitwise xor would return a plausible wrong number, which is the whole
    # failure mode this tool exists to remove.
    ("2^8", "256"),
    # Thousands separators are how a person writes money.
    ("12,500 / 4", "3,125"),
])
def test_it_evaluates_exactly(expression, expected):
    assert expected in one(expression)


def test_a_rate_is_not_dressed_up_as_money():
    """"0.0035 (to 2dp: 0.00)" invites a model to carry the zero forward."""
    assert one("0.042/12") == "0.042/12 = 0.0035"


@pytest.mark.parametrize("expression,fragment", [
    ('__import__("os").system("echo hi")', "only plain function calls"),
    ('open("x")', "not available"),
    ("abc + 1", "not a known constant"),
    ("[i for i in range(3)]", "not arithmetic"),
    ("1/0", "division by zero"),
])
def test_nothing_but_arithmetic_gets_through(expression, fragment):
    assert fragment in one(expression)


def test_an_unbounded_power_is_refused_rather_than_attempted():
    """9**9**9 is not a long calculation, it is one that never returns."""
    assert "above the" in one("9**9**9")


def test_constants_are_available():
    assert repr(math.pi * 2) in one("pi * 2")


def test_the_tool_is_registered_and_takes_several_expressions_at_once():
    tool = get_tool("calculate")
    assert tool is not None
    result = tool.run({"expressions": ["2+2", "3*3"]}, None)
    assert result.ok
    assert "2+2 = 4" in result.content
    assert "3*3 = 9" in result.content


def test_a_single_expression_string_is_accepted():
    """Models send a bare string when the schema says array."""
    tool = get_tool("calculate")
    result = tool.run({"expression": "7*6"}, None)
    assert result.ok and "42" in result.content


def test_no_expression_asks_for_one_rather_than_failing():
    tool = get_tool("calculate")
    result = tool.run({}, None)
    assert not result.ok
    assert "expressions" in result.content

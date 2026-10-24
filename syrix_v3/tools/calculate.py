"""Arithmetic that is actually evaluated rather than recalled.

Added because it was measured wrong. Asked for the maturity value of 12,500 at
4.2% compounded monthly over seven years, SYRIX laid out the right formula, the
right monthly rate and the right period count, then evaluated
``(1.0035)**84`` as 1.32796 and reported £16,599.50. The true factor is
1.341095 and the answer is £16,763.69 — a £164 error, presented under six
steps of correct working, which is the most convincing way possible to be
wrong. Two different lanes made the same mistake, so it is not a weak-model
problem: a language model predicts the digits of a long power, it does not
compute them.

Nothing about showing the working fixes that. The number has to come from an
evaluator, which is what this is.

Deliberately zero-dependency and whitelisted rather than ``eval``: the
expression is parsed to an AST and every node is checked against the set
below, so a name, attribute, subscript, comprehension or call to anything
unlisted is refused before evaluation. ``__``-style attribute escapes cannot
be reached because ``Attribute`` is not an allowed node at all.
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Any, Dict, List

from .registry import Tool, ToolContext, ToolResult, register

_MAX_EXPR_CHARS = 400
_MAX_EXPRESSIONS = 12

# A power is the one operator that can be made to hang the process — 9**9**9
# is not a long calculation, it is an unbounded one — so exponents are bounded
# rather than trusted.
_MAX_EXPONENT = 10000

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCTIONS: Dict[str, Any] = {
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log10": math.log10,
    "log2": math.log2, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "floor": math.floor, "ceil": math.ceil, "fabs": math.fabs,
    "factorial": math.factorial, "gcd": math.gcd, "hypot": math.hypot,
    "degrees": math.degrees, "radians": math.radians,
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "pow": pow,
}
_CONSTANTS: Dict[str, float] = {
    "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
}


class _Refused(ValueError):
    """The expression contains something that is not arithmetic."""


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise _Refused(f"{node.value!r} is not a number")
    if isinstance(node, ast.BinOp):
        op = _BINARY.get(type(node.op))
        if op is None:
            raise _Refused(f"{type(node.op).__name__} is not an allowed operator")
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > _MAX_EXPONENT:
            raise _Refused(
                f"exponent {right} is above the {_MAX_EXPONENT} ceiling")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY.get(type(node.op))
        if op is None:
            raise _Refused(f"{type(node.op).__name__} is not an allowed operator")
        return op(_evaluate(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise _Refused(
            f"{node.id!r} is not a known constant — use a number, or one of "
            + ", ".join(sorted(_CONSTANTS)))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _Refused("only plain function calls are allowed")
        fn = _FUNCTIONS.get(node.func.id)
        if fn is None:
            raise _Refused(
                f"{node.func.id!r} is not available — allowed: "
                + ", ".join(sorted(_FUNCTIONS)))
        if node.keywords:
            raise _Refused("keyword arguments are not supported")
        return fn(*[_evaluate(a) for a in node.args])
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_evaluate(e) for e in node.elts]
    raise _Refused(f"{type(node).__name__} is not arithmetic")


def _format(value: Any) -> str:
    """Full precision, and a two-decimal form when one would be read as money."""
    if isinstance(value, float):
        if value != value or value in (math.inf, -math.inf):
            return repr(value)
        exact = repr(value)
        rounded = f"{value:,.2f}"
        if exact.rstrip("0").rstrip(".") == f"{value:.2f}".rstrip("0").rstrip("."):
            return rounded
        # A two-decimal form of a rate or a small ratio is noise at best and
        # misleading at worst: "0.0035 (to 2dp: 0.00)" invites a model to carry
        # the zero forward. The hint is for money-sized numbers only.
        if abs(value) < 1:
            return exact
        return f"{exact}  (to 2dp: {rounded})"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def one(expression: str) -> str:
    """Evaluate a single expression, returning a line for the model to read."""
    text = str(expression or "").strip()
    if not text:
        return "(empty expression)"
    if len(text) > _MAX_EXPR_CHARS:
        return f"{text[:60]}... — refused, over {_MAX_EXPR_CHARS} characters"
    # `^` means exponentiation everywhere except Python, and a model writing
    # maths reaches for it constantly. Silently treating it as bitwise xor
    # would return a plausible wrong number, which is the failure this tool
    # exists to stop.
    normalised = text.replace("^", "**").replace("×", "*").replace("÷", "/")
    normalised = normalised.replace(",", "")
    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError as exc:
        return f"{text} → cannot be read as arithmetic ({exc.msg})"
    try:
        value = _evaluate(tree)
    except _Refused as exc:
        return f"{text} → refused: {exc}"
    except ZeroDivisionError:
        return f"{text} → division by zero"
    except (OverflowError, ValueError) as exc:
        return f"{text} → {type(exc).__name__}: {exc}"
    return f"{text} = {_format(value)}"


def _run(args: Dict[str, Any], tctx: ToolContext) -> ToolResult:
    raw = args.get("expressions")
    if raw is None:
        raw = args.get("expression")
    if isinstance(raw, str):
        items: List[str] = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(v) for v in raw]
    else:
        items = []
    items = [i for i in (s.strip() for s in items) if i][:_MAX_EXPRESSIONS]
    if not items:
        return ToolResult.problem(
            "Pass the arithmetic as 'expressions', e.g. "
            "[\"12500 * 1.0035**84\", \"16763.69 - 12500\"].")
    lines = [one(expr) for expr in items]
    return ToolResult(content="\n".join(lines),
                      detail=f"{len(lines)} expression(s)")


register(Tool(
    name="calculate",
    description=(
        "Evaluate arithmetic exactly. Use it for EVERY number you would "
        "otherwise work out in your head: powers, compound interest, "
        "percentages, unit conversions, totals, differences, averages. You "
        "cannot reliably evaluate something like 1.0035**84 by predicting its "
        "digits, and a wrong figure presented under correct working is worse "
        "than no figure. Send every step you need in one call — they run "
        "together. Supports + - * / // % ** and sqrt, log, exp, sin, cos, "
        "tan, floor, ceil, abs, round, min, max, sum, factorial, plus pi "
        "and e."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expressions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "One or more arithmetic expressions, e.g. "
                    "[\"12500 * (1 + 0.042/12)**84\", \"0.042/12\"]."
                ),
            },
        },
        "required": ["expressions"],
    },
    run=_run,
    kind="tool_call",
    label="Working it out exactly",
))

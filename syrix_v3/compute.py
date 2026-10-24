"""Exact answers to problems, by writing and running a program.

A language model predicts the digits of a long computation; it does not carry
it out. Measured on the evaluation's harder set, three problems were missed
for exactly that reason — the digit sum of 2^50, 3^2026 + 5^2026 mod 13, and
the days between two dates — each answered confidently, each wrong, each on a
lane that could not call the calculator. Frontier assistants settle this by
running code, and so does this module: a model writes a short program for the
problem, the program runs, and its printed result reaches the answering model
as exact evidence. It works on every lane, because the program is text.

The program is untrusted. Before it runs it must parse, import only
modules from a small mathematical allow-list, and touch no name or attribute
that reaches the file system, the network, the interpreter or another
process. It then runs in a separate, isolated interpreter (``-I``: no user
site, no environment paths) from an empty temporary directory, with a hard
timeout and a cap on what it may print. Anything that fails a check is not
run at all.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional
import syrix_obs

_ALLOWED_MODULES = frozenset({
    "math", "cmath", "fractions", "decimal", "itertools", "functools", "collections",
    "datetime", "calendar", "statistics", "re", "string", "heapq", "bisect", "operator",
    "numbers", "random",
})
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "__import__", "input", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview",
    "object", "classmethod", "staticmethod", "super", "property", "dir",
})
_TIMEOUT_S = 6.0
_MAX_OUTPUT = 2000

_PROMPT = """Write a short Python 3 program that computes the exact answer to the problem below and prints it.

Rules:
- Compute; do not hard-code the answer. Brute force is fine when it is fast.
- Print only the final answer on the last line (a number, fraction, date or short phrase), after any intermediate lines you find useful.
- Allowed imports: math, fractions, decimal, itertools, functools, collections, datetime, calendar, statistics, re, string, heapq, bisect, operator.
- No input(), no files, no network, no other modules.

Reply with one ```python code block and nothing else."""


@dataclass
class Computed:
    ok: bool
    output: str = ""
    code: str = ""
    problem: str = ""
    took_ms: float = 0.0

    def as_evidence(self) -> str:
        if not self.ok:
            return ""
        return (
            "COMPUTED — a program written for this question was run and printed:\n"
            f"{self.output}\n\nThe program:\n```python\n{self.code}\n```\n"
            "This result is exact arithmetic. Use it, and show the working in words. "
            "If the program misread the question, say how and answer the question "
            "as asked; otherwise do not contradict its result."
        )


def check(code: str) -> Optional[str]:
    """Why the program may not run, or None when it may."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"does not parse: {exc.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _ALLOWED_MODULES:
                    return f"imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in _ALLOWED_MODULES:
                return f"imports from {node.module}"
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return f"uses {node.id}"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"reaches {node.attr}"
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await)):
            return "uses a construct outside plain computation"
    return None


def run(code: str, *, timeout_s: float = _TIMEOUT_S) -> Computed:
    """Run a checked program in an isolated interpreter."""
    started = time.monotonic()
    problem = check(code)
    if problem:
        return Computed(False, code=code, problem=problem)
    workdir = tempfile.mkdtemp(prefix="syrix_compute_")
    path = os.path.join(workdir, "program.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "PYTHONIOENCODING": "utf-8"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True,
                              timeout=timeout_s, cwd=workdir, env=env, creationflags=flags,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return Computed(False, code=code, problem="ran out of time",
                        took_ms=round((time.monotonic() - started) * 1000.0, 1))
    finally:
        try:
            os.remove(path)
            os.rmdir(workdir)
        except OSError as exc:
            syrix_obs.report("v3.compute.run", exc, level="debug")
    output = (done.stdout or "").strip()[-_MAX_OUTPUT:]
    took = round((time.monotonic() - started) * 1000.0, 1)
    if done.returncode != 0 or not output:
        err = (done.stderr or "").strip().splitlines()
        return Computed(False, code=code, problem=(err[-1] if err else "printed nothing")[:200],
                        took_ms=took)
    return Computed(True, output=output, code=code, took_ms=took)


def _extract(text: str) -> str:
    match = re.search(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    return (match.group(1) if match else "").strip()


def solve(question: str, *, timeout_s: float = 20.0) -> Computed:
    """Have a program written for the question and run it."""
    from . import gateway
    started = time.monotonic()
    try:
        got = gateway.complete(
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": question[:3000]}],
            tier="T2_BALANCED", max_tokens=1600, temperature=0.1, prompt_chars=6000,
            timeout_s=timeout_s, allow_local_fallback=False, reasoning="medium",
        )
    except Exception as exc:
        return Computed(False, problem=f"no program written: {type(exc).__name__}")
    code = _extract(got.text)
    if not code:
        return Computed(False, problem="the reply held no program")
    result = run(code)
    result.took_ms = round((time.monotonic() - started) * 1000.0, 1)
    return result

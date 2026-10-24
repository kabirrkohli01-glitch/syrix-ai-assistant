"""The compute sandbox: what it refuses to run, and that what it runs is exact."""
from __future__ import annotations

import pytest

from syrix_v3 import compute, gateway


@pytest.mark.parametrize("code,why", [
    ("import os\nos.system('dir')", "imports os"),
    ("import subprocess", "imports subprocess"),
    ("from pathlib import Path", "imports from pathlib"),
    ("import socket", "imports socket"),
    ("open('x.txt').read()", "uses open"),
    ("eval('1+1')", "uses eval"),
    ("__import__('os')", "uses __import__"),
    ("print(().__class__.__bases__[0].__subclasses__())", "reaches __"),
    ("getattr(1, 'real')", "uses getattr"),
    ("from . import thing", "imports from"),
])
def test_anything_that_reaches_outside_arithmetic_is_refused(code, why):
    problem = compute.check(code)
    assert problem is not None and problem.startswith(why.split()[0]) and why.split()[-1].strip("_") in problem


def test_plain_computation_passes_and_runs_exactly():
    code = "from fractions import Fraction\nimport math\nprint(math.comb(10, 3), Fraction(5, 12) * Fraction(4, 11))"
    assert compute.check(code) is None
    got = compute.run(code)
    assert got.ok and got.output == "120 5/33"


def test_a_program_that_never_ends_is_stopped():
    got = compute.run("while True:\n    pass", timeout_s=1.5)
    assert not got.ok and got.problem == "ran out of time"


def test_a_refused_program_is_never_run(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise AssertionError("must not run")
    monkeypatch.setattr(subprocess, "run", boom)
    got = compute.run("import os\nprint(os.getcwd())")
    assert not got.ok and got.problem == "imports os"


def test_solve_runs_what_the_lane_writes(monkeypatch):
    def fake(messages, **kwargs):
        return gateway.Completion(text="```python\nprint(sum(int(d) for d in str(2**50)))\n```")
    monkeypatch.setattr(gateway, "complete", fake)
    got = compute.solve("What is the sum of the digits of 2^50?")
    assert got.ok and got.output == "76"
    assert "76" in got.as_evidence()

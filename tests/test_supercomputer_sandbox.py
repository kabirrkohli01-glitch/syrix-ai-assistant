"""The compute sandbox: it must be useful, and it must not be a way out.

Arbitrary code execution is the largest surface added in this work, so the walls
are asserted rather than assumed. Every "must not" here was found by actually
running the attack against an earlier version of the guard:

* a name blocklist broke pandas and matplotlib, whose internals legitimately
  import pickle, shutil and ctypes — so the check now applies only to the
  agent's own frames;
* patching ``builtins.open`` alone left ``pathlib.Path.write_text`` free to
  write outside the workspace, because it goes through ``io.open``;
* and the sandbox could read ``.env`` and print a live API key to stdout, which
  goes into the transcript, which goes to a cloud model. No socket required.

These are slow-ish (each spawns a real interpreter), which is the price of
testing the thing that actually runs rather than a mock of it.
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import paths, sandbox_exec  # noqa: E402


class _SandboxCase(SupercomputerTestMixin, unittest.TestCase):
    def run_code(self, code, **cfg):
        return sandbox_exec.run("sbx", code, config=cfg or {})

    def workdir(self):
        return paths.task_workspace("sbx")


class TestItActuallyWorks(_SandboxCase):
    def test_it_computes_and_prints(self):
        res = self.run_code("print('total', sum(range(101)))")
        self.assertTrue(res["ok"], res.get("data", {}).get("stderr"))
        self.assertIn("5050", res["data"]["stdout"])

    def test_it_can_write_files_into_the_task_workspace(self):
        res = self.run_code("open('notes.md', 'w').write('# hi')")
        self.assertTrue(res["ok"], res.get("data", {}).get("stderr"))
        self.assertIn("notes.md", res["data"]["files"])
        self.assertTrue(os.path.isfile(os.path.join(self.workdir(), "notes.md")))

    def test_pandas_and_matplotlib_are_usable(self):
        """The point of the sandbox. A guard that blocks these blocks the feature.

        The first version of the import guard did exactly that: `import socket`
        was correctly refused while "analyse this and draw me a chart" also
        failed, which is precisely the wrong way round.
        """
        res = self.run_code(
            "import pandas as pd, matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "df = pd.DataFrame({'t': ['A', 'B'], 'v': [3, 5]})\n"
            "print('mean', df['v'].mean())\n"
            "df.to_excel('out.xlsx', index=False)\n"
            "plt.bar(df['t'], df['v']); plt.savefig('chart.png')\n"
        )
        self.assertTrue(res["ok"], res.get("data", {}).get("stderr", "")[-600:])
        self.assertIn("mean 4.0", res["data"]["stdout"])
        self.assertIn("chart.png", res["data"]["files"])
        self.assertIn("out.xlsx", res["data"]["files"])

    def test_it_does_not_litter_the_workspace_with_pycache(self):
        res = self.run_code("print(1)")
        self.assertTrue(res["ok"])
        self.assertFalse([f for f in res["data"]["files"] if "__pycache__" in f])
        self.assertFalse([f for f in res["data"]["files"] if f.startswith("_syrix_sandbox")])

    def test_python_source_never_reaches_the_command_line(self):
        """SYRIX's global subprocess guard regex-matches the flattened argv for
        shell verbs. Passing the agent's code with `-c` put Python source inside
        that string, so ordinary code was refused as destructive:

            del df['col']        -> matched \\bdel\\b
            "{}".format(x)       -> matched \\bformat\\b
            rm = compute()       -> matched \\brm\\b

        It is not hypothetical — the wrapper's own `del sys.path[0]` was blocked
        the first time the suite ran alongside the tests that install the guard.
        The code goes in a file now, so the argv is flags and paths only.
        """
        for code in (
            "d = {'a': 1}\ndel d['a']\nprint('deleted', d)",
            "print('{} ok'.format(41 + 1))",
            "rm = 5\nrd = 6\nprint('vals', rm, rd)",
            "print('format volume shutdown reboot')",
        ):
            res = self.run_code(code)
            self.assertTrue(res["ok"],
                            f"blocked by the shell-verb guard: {code!r} -> "
                            f"{res.get('reason')} {res.get('data', {}).get('stderr', '')[:200]}")


class TestItCannotEscape(_SandboxCase):
    def assert_blocked(self, code, needle=""):
        res = self.run_code(code)
        self.assertFalse(res["ok"], f"THIS SHOULD HAVE BEEN BLOCKED: {code}")
        if needle:
            self.assertIn(needle, (res.get("data") or {}).get("stderr", ""))

    def test_there_is_no_network(self):
        self.assert_blocked("import socket; socket.socket()")
        self.assert_blocked("import urllib.request; urllib.request.urlopen('http://x.example')")

    def test_there_is_no_shell(self):
        self.assert_blocked("import subprocess; subprocess.run(['cmd'])")
        self.assert_blocked("import os; os.system('dir')")

    def test_it_cannot_write_outside_the_workspace(self):
        outside = os.path.join(_ROOT, "SANDBOX_SHOULD_NOT_EXIST.txt")
        for code in (
            f"open(r'{outside}', 'w').write('x')",
            f"import pathlib; pathlib.Path(r'{outside}').write_text('x')",
            f"import os; os.open(r'{outside}', os.O_CREAT | os.O_WRONLY)",
        ):
            self.assert_blocked(code)
        self.assertFalse(os.path.exists(outside), "a write escaped the sandbox")

    def test_it_cannot_delete_outside_the_workspace(self):
        """The user's line was 'I don't want my files getting deleted'."""
        import tempfile

        victim = os.path.join(tempfile.mkdtemp(prefix="syrix_sbx_victim_"), "keep.txt")
        with open(victim, "w", encoding="utf-8") as fh:
            fh.write("precious")
        self.assert_blocked(f"import os; os.remove(r'{victim}')")
        self.assert_blocked(f"import shutil; shutil.rmtree(r'{os.path.dirname(victim)}')")
        self.assertTrue(os.path.exists(victim), "a delete escaped the sandbox")

    def test_it_cannot_read_secrets(self):
        """No socket needed to exfiltrate: stdout goes into the transcript,
        and the transcript goes to a cloud model."""
        from syrix_supercomputer import workspace

        env = os.path.join(workspace.syrix_repo_root(), ".env")
        for code in (
            f"print(open(r'{env}').read())",
            f"import pathlib; print(pathlib.Path(r'{env}').read_text())",
        ):
            res = self.run_code(code)
            self.assertFalse(res["ok"], "the sandbox read .env")
            self.assertNotIn("API_KEY", (res.get("data") or {}).get("stdout", ""))

    def test_it_cannot_read_the_state_directory(self):
        """Where the connector token store lives."""
        import syrix_paths

        target = os.path.join(syrix_paths.syrix_root(), "connectors")
        res = self.run_code(f"import os; print(os.listdir(r'{target}'))")
        self.assertFalse(res["ok"])

    def test_the_environment_carries_no_credentials(self):
        res = self.run_code(
            "import os\n"
            "print('LEAKS', [k for k in os.environ "
            "if 'KEY' in k.upper() or 'TOKEN' in k.upper() or 'SECRET' in k.upper()])"
        )
        self.assertTrue(res["ok"])
        self.assertIn("LEAKS []", res["data"]["stdout"])

    def test_a_runaway_is_killed(self):
        res = self.run_code("while True: pass", supercomputer_sandbox_timeout_s=5)
        self.assertFalse(res["ok"])
        self.assertTrue(res["reason"].startswith("timed_out_after_"))
        self.assertTrue(res["data"]["timed_out"])

    def test_an_error_is_reported_rather_than_swallowed(self):
        res = self.run_code("raise ValueError('deliberate')")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "error")
        self.assertIn("deliberate", res["data"]["stderr"])

    def test_empty_code_is_refused_without_spawning_anything(self):
        self.assertEqual(self.run_code("")["reason"], "empty_code")


if __name__ == "__main__":
    unittest.main()

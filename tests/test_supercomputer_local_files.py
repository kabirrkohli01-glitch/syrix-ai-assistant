"""Reading the user's own files: what is reachable, and what must never be.

"Submit my CV, it's in my Downloads" needs the mode to see a file on the machine
it runs on. That is a real expansion of what SYRIX touches, so the boundaries
are asserted here rather than trusted: an allowlist of folders, containment
checked on the realpath, executables refused outright, and SYRIX's own repo off
limits.

Every one of these is a refusal test. The capability is only acceptable because
they hold.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supercomputer_testkit import SupercomputerTestMixin  # noqa: E402

from syrix_supercomputer import local_files  # noqa: E402


class _FilesCase(SupercomputerTestMixin, unittest.TestCase):
    """Points the allowlist at a temp tree instead of the real home folder."""

    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp(prefix="syrix_lf_home_")
        self.downloads = os.path.join(self.home, "Downloads")
        os.makedirs(self.downloads, exist_ok=True)
        self.cv = os.path.join(self.downloads, "cv.txt")
        with open(self.cv, "w", encoding="utf-8") as fh:
            fh.write("Kabirr Kohli — engineer")
        # Somewhere plausible and OUTSIDE every allowed root.
        self.secret_dir = tempfile.mkdtemp(prefix="syrix_lf_secret_")
        self.secret = os.path.join(self.secret_dir, "private.txt")
        with open(self.secret, "w", encoding="utf-8") as fh:
            fh.write("not for the agent")

        self._real_home = local_files._home
        local_files._home = lambda: self.home
        self.cfg = {"supercomputer_local_read_roots": "downloads"}

    def tearDown(self):
        local_files._home = self._real_home
        import shutil

        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.secret_dir, ignore_errors=True)
        super().tearDown()


class TestWhatIsReachable(_FilesCase):
    def test_a_file_in_an_allowed_folder_resolves(self):
        target, reason = local_files.resolve("downloads/cv.txt", self.cfg)
        self.assertEqual(reason, "")
        self.assertEqual(os.path.realpath(target), os.path.realpath(self.cv))

    def test_a_bare_filename_is_found_in_the_allowed_roots(self):
        target, _ = local_files.resolve("cv.txt", self.cfg)
        self.assertIsNotNone(target)

    def test_an_absolute_path_inside_an_allowed_root_resolves(self):
        target, reason = local_files.resolve(self.cv, self.cfg)
        self.assertEqual(reason, "")
        self.assertIsNotNone(target)

    def test_reading_returns_the_text(self):
        res = local_files.read_file("downloads/cv.txt", self.cfg)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertIn("Kabirr", res["data"]["text"])

    def test_listing_shows_the_folder(self):
        res = local_files.list_folder("downloads", self.cfg)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertIn("cv.txt", [e["name"] for e in res["data"]["entries"]])

    def test_listing_nothing_names_the_readable_folders(self):
        res = local_files.list_folder("", self.cfg)
        self.assertTrue(res["ok"])
        self.assertTrue(res["data"]["entries"])


class TestWhatIsRefused(_FilesCase):
    def test_a_path_outside_every_allowed_root_is_refused(self):
        target, reason = local_files.resolve(self.secret, self.cfg)
        self.assertIsNone(target)
        self.assertEqual(reason, "outside_allowed_folders")

    def test_dot_dot_traversal_cannot_climb_out(self):
        target, reason = local_files.resolve(
            os.path.join("downloads", "..", "..", "private.txt"), self.cfg)
        self.assertIsNone(target)
        self.assertNotEqual(reason, "")

    def test_a_folder_not_on_the_allowlist_is_refused(self):
        os.makedirs(os.path.join(self.home, "Music"), exist_ok=True)
        target, reason = local_files.resolve(
            os.path.join(self.home, "Music", "x.txt"), self.cfg)
        self.assertIsNone(target)
        self.assertEqual(reason, "outside_allowed_folders")

    def test_executables_are_refused_whatever_the_approval(self):
        exe = os.path.join(self.downloads, "installer.exe")
        with open(exe, "wb") as fh:
            fh.write(b"MZ")
        target, reason = local_files.resolve("downloads/installer.exe", self.cfg)
        self.assertIsNone(target)
        self.assertTrue(reason.startswith("refused_file_type:"))
        # …and they do not even appear in a listing, so nothing can pick one.
        res = local_files.list_folder("downloads", self.cfg)
        self.assertNotIn("installer.exe", [e["name"] for e in res["data"]["entries"]])

    def test_an_executable_can_never_be_attached_to_a_form(self):
        """The upload path must be no looser than the read path."""
        exe = os.path.join(self.downloads, "payload.bat")
        with open(exe, "w", encoding="utf-8") as fh:
            fh.write("echo hi")
        target, reason = local_files.attachable("downloads/payload.bat", self.cfg)
        self.assertIsNone(target)
        self.assertTrue(reason.startswith("refused_file_type:"))

    def test_syrix_own_repo_is_refused(self):
        """An agent reading the assistant that is running it is a category of
        mistake worth simply removing."""
        from syrix_supercomputer import workspace

        repo = workspace.syrix_repo_root()
        cfg = dict(self.cfg)
        # Even if a root somehow pointed at the repo, the check still catches it.
        self.assertTrue(workspace.is_syrix_path(os.path.join(repo, "config.json")))
        target, reason = local_files.resolve(os.path.join(repo, "config.json"), cfg)
        self.assertIsNone(target)

    def test_a_missing_file_is_distinguished_from_a_forbidden_one(self):
        """They need different answers: one is 'try again', one is 'never'."""
        _, missing = local_files.resolve("downloads/nope.txt", self.cfg)
        self.assertEqual(missing, "not_found")
        _, outside = local_files.resolve(self.secret, self.cfg)
        self.assertEqual(outside, "outside_allowed_folders")

    def test_every_refusal_has_a_human_explanation(self):
        for reason in ("not_found", "outside_allowed_folders", "refused_syrix_repo",
                       "file_too_large", "refused_file_type:.exe"):
            said = local_files.describe_reason(reason, self.cfg)
            self.assertTrue(said and said != reason, reason)


class TestConfiguration(_FilesCase):
    def test_an_unknown_root_name_is_ignored_not_obeyed(self):
        """A config typo must not open up somewhere it should not."""
        names = local_files.configured_root_names({"supercomputer_local_read_roots": "C:\\, sneaky"})
        self.assertEqual(names, list(local_files.DEFAULT_ROOTS))

    def test_the_default_roots_are_the_four_boss_agreed(self):
        self.assertEqual(local_files.DEFAULT_ROOTS,
                         ("downloads", "documents", "desktop", "pictures"))


if __name__ == "__main__":
    unittest.main()

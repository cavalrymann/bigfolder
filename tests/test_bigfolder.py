"""Tests for bigfolder. Run with:  python3 -m unittest discover -s tests -v"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bigfolder  # noqa: E402

APPLY = ("--apply", "--no-keep-awake")


def run(*argv):
    """Run bigfolder quietly; return (exit code, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = bigfolder.main([str(a) for a in argv])
    return code, out.getvalue()


def files_in(folder: Path) -> set[str]:
    """All files below folder, as relative paths with "/"."""
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


class Base(unittest.TestCase):
    """A temporary source folder, destination and state folder per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"
        self.dest = self.root / "dest"
        self.src.mkdir()
        self._env = os.environ.get("BIGFOLDER_STATE_DIR")
        os.environ["BIGFOLDER_STATE_DIR"] = str(self.root / "state")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("BIGFOLDER_STATE_DIR", None)
        else:
            os.environ["BIGFOLDER_STATE_DIR"] = self._env
        self._tmp.cleanup()

    def make(self, rel, date=(2024, 5, 3), data=b"pixels"):
        """Create a file in the source folder with a given file date."""
        path = self.src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stamp = datetime(*date, 12).timestamp()
        os.utime(path, (stamp, stamp))
        return path


class SplitFolder(Base):
    """A huge flat folder, divided into parts."""

    def test_preview_changes_nothing(self):
        for n in ("a.jpg", "b.jpg"):
            self.make(n)
        code, text = run("split", self.src, self.dest)
        self.assertEqual((code, "PREVIEW" in text), (0, True))
        self.assertEqual(files_in(self.src), {"a.jpg", "b.jpg"})
        self.assertFalse(self.dest.exists() or (self.root / "state").exists())

    def test_split_into_parts(self):
        for i in range(5):
            self.make(f"f{i}.jpg")
        run("split", self.src, self.dest, "--size", 2, "--sort", "name", *APPLY)
        self.assertEqual(files_in(self.src), set())
        self.assertEqual(files_in(self.dest), {
            "Part 001/f0.jpg", "Part 001/f1.jpg",
            "Part 002/f2.jpg", "Part 002/f3.jpg", "Part 003/f4.jpg"})

    def test_skips_helper_files(self):
        for n in ("a.jpg", "._a.jpg", ".DS_Store", "Thumbs.db"):
            self.make(n)
        run("split", self.src, self.dest, *APPLY)
        self.assertEqual(files_in(self.dest), {"Part 001/a.jpg"})

    def test_filters(self):
        for n in ("a.jpg", "b.PNG", "c.pdf", "Tool.app_icon.png"):
            self.make(n)
        run("split", self.src, self.dest, "--preset", "photos", "--exclude", r"\.app_", *APPLY)
        self.assertEqual(files_in(self.dest), {"Part 001/a.jpg", "Part 001/b.PNG"})

    def test_never_overwrites(self):
        self.make("same.jpg")
        (self.dest / "Part 001").mkdir(parents=True)
        (self.dest / "Part 001" / "same.jpg").write_bytes(b"existing")
        run("split", self.src, self.dest, *APPLY)
        self.assertEqual((self.dest / "Part 001" / "same.jpg").read_bytes(), b"existing")
        self.assertTrue((self.dest / "Part 001" / "same-1.jpg").exists())

    def test_resume_keeps_part_numbers(self):
        for i in range(7):
            self.make(f"f{i}.jpg")
        args = ("split", self.src, self.dest, "--size", 3, "--sort", "name", *APPLY)
        run(*args, "--limit", 4)
        code, text = run(*args)
        self.assertEqual(code, 0)
        self.assertIn("Resuming", text)
        self.assertIn("FINISHED", text)
        self.assertEqual(files_in(self.dest), {
            "Part 001/f0.jpg", "Part 001/f1.jpg", "Part 001/f2.jpg",
            "Part 002/f3.jpg", "Part 002/f4.jpg", "Part 002/f5.jpg", "Part 003/f6.jpg"})

    def test_priority_first(self):
        for n in ("a.png", "b.png", "z.jpg"):
            self.make(n)
        run("split", self.src, self.dest, "--size", 1, "--sort", "name",
            "--priority", "jpg", *APPLY)
        self.assertIn("Part 001/z.jpg", files_in(self.dest))

    def test_copy_keeps_originals(self):
        self.make("a.jpg")
        run("split", self.src, self.dest, "--copy", *APPLY)
        self.assertEqual(files_in(self.src), {"a.jpg"})
        self.assertEqual(files_in(self.dest), {"Part 001/a.jpg"})

    def test_by_month(self):
        self.make("old.jpg", (2015, 6, 10))
        run("split", self.src, self.dest, "--by", "month", *APPLY)
        self.assertEqual(files_in(self.dest), {"2015/2015-06/old.jpg"})

    def test_saved_name_list_and_missing_files(self):
        self.make("a.jpg")
        self.make("b.jpg")
        names = self.root / "names.txt"
        names.write_text("a.jpg\ngone.jpg\n")
        code, text = run("split", self.src, self.dest, "--names", names, *APPLY)
        self.assertEqual(code, 0)
        self.assertIn("1 no longer there", text)
        self.assertEqual(files_in(self.dest), {"Part 001/a.jpg"})

    def test_dest_inside_src(self):
        self.make("a.jpg")
        inner = self.src / "Parts"
        run("split", self.src, inner, *APPLY)
        run("split", self.src, inner, "--restart", *APPLY)
        self.assertEqual(files_in(self.src), {"Parts/Part 001/a.jpg"})

    def test_scan(self):
        for n in ("a.jpg", "b.JPG", "c.png", "._a.jpg"):
            self.make(n)
        out = self.root / "names.txt"
        code, text = run("scan", self.src, "--out", out)
        self.assertEqual(code, 0)
        self.assertIn("Files directly in folder: 4", text)
        self.assertIn("Helper files (._*, .DS_Store, ...): 1", text)
        self.assertEqual(len(out.read_text().split()), 4)


class ImportCard(Base):
    """A camera card: DCIM folders, RAW + JPG pairs, sidecars, copied by date."""

    CARD = ("-r", "--by", "day", "--copy")

    def test_copied_by_day_card_untouched(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", (2024, 5, 3))
        self.make("DCIM/100CANON/IMG_0002.JPG", (2024, 5, 4))
        code, _ = run("split", self.src, self.dest, *self.CARD, *APPLY)
        self.assertEqual(code, 0)
        self.assertEqual(files_in(self.dest), {
            "2024/2024-05-03/IMG_0001.JPG", "2024/2024-05-04/IMG_0002.JPG"})
        self.assertEqual(len(files_in(self.src)), 2)

    def test_hidden_card_folders_skipped(self):
        self.make("DCIM/100CANON/IMG_0001.JPG")
        self.make(".Trashes/501/IMG_0099.JPG")
        run("split", self.src, self.dest, *self.CARD, *APPLY)
        self.assertEqual(files_in(self.dest), {"2024/2024-05-03/IMG_0001.JPG"})

    def test_pairs_never_split_across_parts(self):
        for stem in "ABC":
            self.make(f"DCIM/100CANON/{stem}.DNG")
            self.make(f"DCIM/100CANON/{stem}.JPG")
        run("split", self.src, self.dest, "-r", "--keep-pairs", "--size", 3, "--copy", *APPLY)
        self.assertEqual(files_in(self.dest), {
            "Part 001/A.DNG", "Part 001/A.JPG", "Part 002/B.DNG", "Part 002/B.JPG",
            "Part 003/C.DNG", "Part 003/C.JPG"})

    def test_sidecar_follows_photo_date(self):
        self.make("DCIM/100CANON/IMG_0001.DNG", (2024, 5, 3))
        self.make("DCIM/100CANON/IMG_0001.xmp", (2026, 10, 1))   # edited later
        run("split", self.src, self.dest, *self.CARD, "--keep-pairs", *APPLY)
        self.assertEqual(files_in(self.dest), {
            "2024/2024-05-03/IMG_0001.DNG", "2024/2024-05-03/IMG_0001.xmp"})

    def test_pair_clash_shares_one_counter(self):
        self.make("DCIM/100CANON/IMG_0001.DNG")
        self.make("DCIM/100CANON/IMG_0001.JPG")
        day = self.dest / "2024" / "2024-05-03"
        day.mkdir(parents=True)
        (day / "IMG_0001.JPG").write_bytes(b"from an earlier card")
        run("split", self.src, self.dest, *self.CARD, "--keep-pairs", *APPLY)
        self.assertEqual((day / "IMG_0001.JPG").read_bytes(), b"from an earlier card")
        self.assertTrue((day / "IMG_0001-1.JPG").exists() and (day / "IMG_0001-1.DNG").exists())

    def test_same_names_in_two_camera_folders(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", data=b"first")
        self.make("DCIM/101CANON/IMG_0001.JPG", data=b"second")
        run("split", self.src, self.dest, *self.CARD, *APPLY)
        day = self.dest / "2024" / "2024-05-03"
        self.assertEqual({p.read_bytes() for p in day.iterdir()}, {b"first", b"second"})

    def test_verified_copy(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", data=os.urandom(50_000))
        code, text = run("split", self.src, self.dest, *self.CARD, "--verify", *APPLY)
        self.assertEqual(code, 0)
        self.assertIn("checksum-verified", text)

    def test_interrupted_copy_leaves_no_half_file(self):
        self.make("DCIM/100CANON/IMG_0001.DNG", data=b"x" * 10_000)
        real_copy = bigfolder.shutil.copyfile

        def copy_then_ctrl_c(src, dst):
            Path(dst).write_bytes(b"half")                         # partly written
            raise KeyboardInterrupt

        bigfolder.shutil.copyfile = copy_then_ctrl_c
        try:
            code, _ = run("split", self.src, self.dest, *self.CARD, *APPLY)
        finally:
            bigfolder.shutil.copyfile = real_copy
        self.assertEqual(code, 130)
        self.assertEqual(files_in(self.dest), set())               # nothing half-written
        run("split", self.src, self.dest, *self.CARD, *APPLY)      # resume
        done = self.dest / "2024/2024-05-03/IMG_0001.DNG"
        self.assertEqual(done.read_bytes(), b"x" * 10_000)
        self.assertEqual(len(files_in(self.dest)), 1)              # no "-1" duplicate

    def test_copy_keeps_file_dates(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", (2019, 8, 1))
        run("split", self.src, self.dest, "-r", "--copy", *APPLY)
        copied = self.dest / "Part 001" / "IMG_0001.JPG"
        self.assertEqual(datetime.fromtimestamp(copied.stat().st_mtime).year, 2019)

    def test_scan_with_sizes(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", data=b"x" * 2000)
        self.make("DCIM/100CANON/IMG_0001.DNG", data=b"x" * 3000)
        code, text = run("scan", self.src, "--recursive", "--sizes")
        self.assertEqual(code, 0)
        self.assertIn("Files in folder and subfolders: 2", text)
        self.assertIn("Total size (without helper files): 5.0 KB", text)

    def test_same_card_twice_adds_nothing(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", data=b"one")
        self.make("DCIM/100CANON/IMG_0002.JPG", data=b"two!")
        args = ("split", self.src, self.dest, *self.CARD, "--verify", *APPLY)
        run(*args)
        code, text = run(*args)
        self.assertEqual(code, 0)
        self.assertIn("2 already there", text)
        self.assertEqual(len(files_in(self.dest)), 2)

    def test_next_card_at_same_place(self):
        self.make("DCIM/100CANON/IMG_0001.JPG", data=b"card one")
        args = ("split", self.src, self.dest, *self.CARD, *APPLY)
        run(*args)
        shutil.rmtree(self.src)                                    # swap the card
        self.make("DCIM/100CANON/IMG_0002.JPG", (2024, 6, 1), data=b"card two")
        self.assertEqual(run(*args)[0], 0)
        self.assertEqual(files_in(self.dest), {
            "2024/2024-05-03/IMG_0001.JPG", "2024/2024-06-01/IMG_0002.JPG"})

    def test_paused_run_on_other_card_refused(self):
        for k in range(4):
            self.make(f"DCIM/100CANON/IMG_000{k}.JPG")
        args = ("split", self.src, self.dest, "-r", "--copy", *APPLY)
        run(*args, "--limit", 2)
        shutil.rmtree(self.src)                                    # a different card
        self.make("DCIM/100CANON/OTHER.JPG")
        self.assertEqual(run(*args)[0], 2)                         # asks for --restart
        self.assertEqual(run(*args, "--restart")[0], 0)
        self.assertIn("Part 001/OTHER.JPG", files_in(self.dest))

    @unittest.skipUnless(shutil.which("exiftool"), "exiftool not installed")
    def test_date_taken_from_exif(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        path = self.src / "DCIM/100CANON/IMG_0001.JPG"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (64, 48), "gray").save(path)
        subprocess.run(["exiftool", "-q", "-overwrite_original",
                        "-DateTimeOriginal=2021:07:14 09:30:00", str(path)], check=True)
        stamp = datetime(2026, 1, 1).timestamp()                   # wrong file date
        os.utime(path, (stamp, stamp))
        run("split", self.src, self.dest, *self.CARD, "--exif", *APPLY)
        self.assertEqual(files_in(self.dest), {"2021/2021-07-14/IMG_0001.JPG"})


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
bigfolder - split a huge folder into small, browsable subfolders.

Two typical jobs:

  * A huge flat folder (tens or hundreds of thousands of loose files) that
    Finder or Explorer can't open: divide it into "Part 001", "Part 002", ...
  * A camera's SD card: copy every photo, sorted by the day it was taken
    ("2024/2024-05-03"), with RAW + JPG + sidecar files kept together and
    every copy verified.

Safe by default:
  * preview first: nothing changes unless you add --apply
  * never overwrites: name clashes become "name-1.ext", "name-2.ext", ...
  * never deletes: a move within one disk is a rename; across disks the
    original is only removed after the copy has been checked;
    --copy keeps all originals (always use it for memory cards)
  * resumable: stop with Ctrl+C, run the same command again to continue

Run "python3 bigfolder.py split --help" for all options.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

__version__ = "0.2.0"

# File names that are never real content (macOS/Windows helper files).
DEFAULT_EXCLUDE = [
    r"^\._",            # macOS "AppleDouble" metadata files on non-Mac disks
    r"^\.DS_Store$",    # macOS Finder settings
    r"^Thumbs\.db$",    # Windows thumbnail cache
    r"^desktop\.ini$",  # Windows folder settings
]

RAW = ("cr2 cr3 crw nef nrw arw srf sr2 orf rw2 raf dng pef srw x3f 3fr iiq "
       "rwl erf mef mos kdc")

PRESETS = {
    "photos": "jpg jpeg heic heif png gif tif tiff bmp webp " + RAW,
    "raw": RAW,
    "camera": "jpg jpeg heic heif tif tiff " + RAW + " xmp mov mp4 mts m2ts avi",
    "sidecars": "xmp thm aae",
    "documents": "pdf doc docx rtf odt pages txt md xls xlsx csv ods numbers ppt pptx odp key",
    "videos": "mov mp4 m4v avi mkv mts m2ts 3gp wmv",
}

# Files that describe another file rather than being a picture themselves.
# With --keep-pairs they follow the picture they belong to.
SIDECAR_EXTS = {"xmp", "thm", "aae", "pp3", "dop", "on1"}

SAVE_EVERY = 25   # write the resume state after this many files
BLOCK = 100       # files handled per block (dates are looked up per block)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def ext_of(name: str) -> str:
    """Lower-case extension without the dot ("DCIM/IMG_1.JPG" -> "jpg")."""
    return os.path.splitext(name)[1][1:].lower()


def parse_exts(text: str | None) -> set[str]:
    """"jpg, .PNG tif" -> {"jpg", "png", "tif"}."""
    if not text:
        return set()
    return {e.strip().lstrip(".").lower() for e in re.split(r"[,\s]+", text) if e.strip()}


def group_key(rel: str) -> tuple[str, str]:
    """Files that belong together: same folder, same name up to the first dot.

    IMG_0001.CR3, IMG_0001.JPG, IMG_0001.xmp and IMG_0001.CR3.xmp share one key.
    """
    folder, base = os.path.split(rel)
    return folder.lower(), base.split(".")[0].lower()


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.2f} TB"


def list_files(src: Path, recursive: bool, skip: list[Path] = ()) -> list[str]:
    """Files inside src, as relative paths with "/" separators.

    Without recursive: one pass over src itself (no per-file lookups).
    With recursive: also every subfolder, except hidden ones (".Trashes",
    ".Spotlight-V100", ...) and the folders in skip.
    """
    if not recursive:
        names = []
        with os.scandir(src) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        names.append(entry.name)
                except OSError:
                    continue
        return names

    skip_real = {os.path.realpath(p) for p in skip}
    out = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".")
                         and os.path.realpath(os.path.join(root, d)) not in skip_real)
        rel_root = os.path.relpath(root, src)
        for f in sorted(files):
            if os.path.islink(os.path.join(root, f)):
                continue
            rel = f if rel_root == "." else os.path.join(rel_root, f)
            out.append(rel.replace(os.sep, "/"))
    return out


def read_names_file(path: Path) -> list[str]:
    names = []
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        for line in fh:
            name = line.rstrip("\n").rstrip("\r")
            if not name or name in (".", "..") or name.startswith("/"):
                continue
            if ".." in name.split("/"):
                continue
            names.append(name)
    return names


def build_plan(names, include, excludes, priority, sort, keep_pairs):
    """Filter and order the files.

    Returns (names, group_ids): files that belong together (with keep_pairs)
    sit next to each other and share a group id.
    """
    rx = [re.compile(p, re.IGNORECASE) for p in excludes]
    kept = []
    for n in names:
        base = os.path.basename(n)
        if base in (".", ".."):
            continue
        if include and ext_of(n) not in include:
            continue
        if any(r.search(base) for r in rx):
            continue
        kept.append(n)
    if sort == "name":
        kept.sort(key=str.lower)

    groups: dict = {}
    for n in kept:
        key = group_key(n) if keep_pairs else n
        groups.setdefault(key, []).append(n)
    ordered = list(groups.values())
    if priority:
        first = [g for g in ordered if any(ext_of(n) in priority for n in g)]
        rest = [g for g in ordered if not any(ext_of(n) in priority for n in g)]
        ordered = first + rest

    out, ids = [], []
    for gid, members in enumerate(ordered):
        out.extend(members)
        ids.extend([gid] * len(members))
    return out, ids


def assign_parts(ids: list[int], size: int) -> list[int]:
    """Part number (0-based) for every file; a group never spans two parts."""
    parts, part, filled, i = [], 0, 0, 0
    while i < len(ids):
        j = i
        while j < len(ids) and ids[j] == ids[i]:
            j += 1
        if filled and filled + (j - i) > size:
            part, filled = part + 1, 0
        parts.extend([part] * (j - i))
        filled += j - i
        i = j
    return parts


def with_counter(name: str, k: int, first_dot: bool) -> str:
    """name -> name-k, inserted before the extension.

    first_dot: "IMG_1.CR3.xmp" -> "IMG_1-1.CR3.xmp" (keeps pairs matching);
    otherwise:  "my.photo.jpg" -> "my.photo-1.jpg".
    """
    if first_dot:
        if "." not in name[1:]:
            return f"{name}-{k}"
        idx = name.index(".", 1)
        return f"{name[:idx]}-{k}{name[idx:]}"
    stem, ext = os.path.splitext(name)
    return f"{stem}-{k}{ext}"


def unique_targets(directory: Path, basenames: list[str], first_dot: bool) -> list[Path]:
    """Free target paths for a group of files, all with the same counter.

    Nothing is ever overwritten. If one file of a RAW+JPG pair clashes, both
    get the same "-1", so they still match.
    """
    k = 0
    while True:
        names = basenames if k == 0 else [with_counter(b, k, first_dot) for b in basenames]
        targets = [directory / n for n in names]
        if len(set(names)) == len(names) and not any(os.path.lexists(t) for t in targets):
            return targets
        k += 1


def already_there(src_path: Path, target: Path, verify: bool) -> bool:
    """True if target exists and is the same file (same size; with verify, same checksum).

    Makes re-running an import safe: photos copied last time are skipped
    instead of being copied again as "IMG_0001-1.JPG".
    """
    try:
        if not target.is_file() or target.stat().st_size != src_path.stat().st_size:
            return False
    except OSError:
        return False
    return not verify or sha256(target) == sha256(src_path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def transfer(src_path: Path, target: Path, copy: bool, verify: bool) -> int:
    """Move (rename) or copy one file to a target that does not exist yet.

    Copies go to a hidden temporary file first and get their real name only
    after the check (size, with verify also SHA-256), so an interrupted copy
    never leaves a half-written photo behind. A move to another disk only
    removes the original after that check. Returns bytes.
    """
    if not copy:
        try:
            os.rename(src_path, target)
            return os.path.getsize(target)
        except OSError as e:
            if e.errno != errno.EXDEV:   # EXDEV = source and target on different disks
                raise
    tmp = target.with_name(f".{target.name}.bigfolder-part")
    try:
        shutil.copyfile(src_path, tmp)
        try:
            shutil.copystat(src_path, tmp)   # keep the file dates
        except OSError:
            pass                             # some disks refuse flags; dates usually stick
        size = os.path.getsize(tmp)
        ok = size == os.path.getsize(src_path)
        if ok and verify:
            ok = sha256(tmp) == sha256(src_path)
        if not ok:
            raise OSError(f"copy check failed for {src_path}; nothing removed")
        os.rename(tmp, target)
    except BaseException:                     # includes Ctrl+C
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    if not copy:
        os.remove(src_path)
    return size


# --------------------------------------------------------------------------
# Dates (for --by day / month / year)
# --------------------------------------------------------------------------

def mtime_dates(src: Path, names: list[str]) -> dict[str, datetime | None]:
    out = {}
    for n in names:
        try:
            out[n] = datetime.fromtimestamp(os.stat(src / n).st_mtime)
        except OSError:
            out[n] = None
    return out


def exif_dates(src: Path, names: list[str]) -> dict[str, datetime | None]:
    """Date taken (or creation date) via exiftool, falling back to the file date."""
    exe = shutil.which("exiftool")
    if not exe:
        raise SystemExit("--exif needs exiftool: https://exiftool.org")
    out: dict[str, datetime | None] = {}
    if names:
        args = [exe, "-j", "-q", "-q", "-fast", "-DateTimeOriginal", "-CreateDate",
                "-d", "%Y-%m-%d %H:%M:%S"] + [f"./{n}" for n in names]
        result = subprocess.run(args, cwd=src, capture_output=True, text=True,
                                errors="surrogateescape")
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            data = []
        for item in data:
            name = item.get("SourceFile", "")
            name = name[2:] if name.startswith("./") else name
            for key in ("DateTimeOriginal", "CreateDate"):
                value = item.get(key)
                if isinstance(value, str) and not value.startswith("0000"):
                    try:
                        out[name] = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                        break
                    except ValueError:
                        continue
    missing = [n for n in names if out.get(n) is None]
    out.update(mtime_dates(src, missing))
    return out


def group_dates(block, block_ids, dates):
    """Give every member of a group the date of its picture, not its sidecar."""
    members: dict[int, list[str]] = {}
    for n, g in zip(block, block_ids):
        members.setdefault(g, []).append(n)
    out = {}
    for group in members.values():
        primary = [m for m in group if ext_of(m) not in SIDECAR_EXTS] or group
        date = next((dates[m] for m in primary if dates.get(m)), None) \
            or next((dates[m] for m in group if dates.get(m)), None)
        for m in group:
            out[m] = date
    return out


# --------------------------------------------------------------------------
# Resume state
# --------------------------------------------------------------------------

def state_dir() -> Path:
    return Path(os.environ.get("BIGFOLDER_STATE_DIR", Path.home() / ".bigfolder"))


def state_path(src: Path, dest: Path, mode_key: str) -> Path:
    key = f"{src.resolve()}|{dest.resolve()}|{mode_key}"
    return state_dir() / f"state-{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def load_state(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            state = json.load(fh)
        return state if state.get("version") == 2 else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_scan(args) -> int:
    src = Path(args.src)
    if not src.is_dir():
        print(f"Not a folder: {src}", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    names = list_files(src, args.recursive)
    elapsed = time.monotonic() - t0
    if args.out:
        with open(args.out, "w", encoding="utf-8", errors="surrogateescape") as fh:
            fh.writelines(n + "\n" for n in names)
    rx = [re.compile(p, re.IGNORECASE) for p in DEFAULT_EXCLUDE]
    is_helper = [any(r.search(os.path.basename(n)) for r in rx) for n in names]
    exts = Counter(ext_of(n) or "(none)" for n, h in zip(names, is_helper) if not h)
    where = "in folder and subfolders" if args.recursive else "directly in folder"
    print(f"Files {where}: {len(names)}  (listed in {fmt_duration(elapsed)})")
    print(f"Helper files (._*, .DS_Store, ...): {sum(is_helper)}")
    sizes: Counter = Counter()
    if args.sizes:
        for n, h in zip(names, is_helper):
            if not h:
                try:
                    sizes[ext_of(n) or "(none)"] += os.stat(src / n).st_size
                except OSError:
                    pass
        print(f"Total size (without helper files): {fmt_size(sum(sizes.values()))}")
    print("Most common types:")
    for ext, count in exts.most_common(args.top):
        extra = f"  {fmt_size(sizes[ext]):>10}" if args.sizes else ""
        print(f"  {count:>9}  {ext:<8}{extra}")
    if args.out:
        print(f"Name list saved to: {args.out}")
    return 0


def cmd_split(args) -> int:
    src = Path(args.src)
    dest = Path(args.dest)
    if not src.is_dir():
        print(f"Not a folder: {src}", file=sys.stderr)
        return 2

    include = parse_exts(args.ext)
    for preset in args.preset or []:
        include |= parse_exts(PRESETS[preset])
    excludes = ([] if args.no_default_exclude else list(DEFAULT_EXCLUDE)) + (args.exclude or [])
    priority = parse_exts(args.priority)

    mode_key = "|".join([args.by, str(args.size), args.prefix,
                         "copy" if args.copy else "move",
                         "rec" if args.recursive else "flat",
                         "pairs" if args.keep_pairs else "single"])
    spath = state_path(src, dest, mode_key)
    state = None if args.restart else load_state(spath)

    if state and state["next"] >= len(state["names"]):
        print("The last run with these settings finished; planning again "
              "(files that are already there will be skipped).")
        state = None
    if state:
        names, ids, parts = state["names"], state["groups"], state["parts"]
        start = state["next"]
        if not os.path.lexists(src / names[start]):
            print(f"The next file from the saved plan is not in {src}:\n  {names[start]}\n"
                  "Is this a different folder or card than last time? "
                  "Run again with --restart to plan from scratch.", file=sys.stderr)
            return 2
        print(f"Resuming: {start} of {len(names)} already handled "
              f"(state file: {spath}). Use --restart to start over.")
    else:
        t0 = time.monotonic()
        if args.names:
            raw = read_names_file(Path(args.names))
        else:
            raw = list_files(src, args.recursive, skip=[dest])
        print(f"Read {len(raw)} names in {fmt_duration(time.monotonic() - t0)}.")
        names, ids = build_plan(raw, include, excludes, priority, args.sort, args.keep_pairs)
        parts = assign_parts(ids, args.size) if args.by == "count" else None
        start = 0
        state = {"version": 2, "src": str(src), "dest": str(dest), "mode": mode_key,
                 "names": names, "groups": ids, "parts": parts, "next": 0,
                 "moved": 0, "bytes": 0, "missing": 0, "already": 0, "failed": []}

    total = len(names)
    width = max(3, len(str((max(parts) + 1) if parts else 1)))

    def folder_for(i: int, date: datetime | None) -> Path:
        if args.by == "count":
            return dest / f"{args.prefix} {parts[i] + 1:0{width}d}"
        if date is None:
            return dest / "Unknown date"
        if args.by == "year":
            return dest / f"{date:%Y}"
        if args.by == "month":
            return dest / f"{date:%Y}" / f"{date:%Y-%m}"
        return dest / f"{date:%Y}" / f"{date:%Y-%m-%d}"

    def dates_for(lo: int, hi: int) -> dict:
        if args.by == "count":
            return {}
        block = names[lo:hi]
        raw_dates = exif_dates(src, block) if args.exif else mtime_dates(src, block)
        return group_dates(block, ids[lo:hi], raw_dates)

    def group_end(i: int, limit: int) -> int:
        j = i + 1
        while j < limit and ids[j] == ids[i]:
            j += 1
        return j

    verb = "copy" if args.copy else "move"

    # ---------------- preview ----------------
    if not args.apply:
        print(f"\nPREVIEW - nothing will be changed. Add --apply to {verb} for real.\n")
        print(f"Files to {verb}: {total - start}" + (f" (of {total}, resuming)" if start else ""))
        exts = Counter(ext_of(n) or "(none)" for n in names[start:])
        print("By type: " + ", ".join(f"{e} {c}" for e, c in exts.most_common(10)))
        if args.keep_pairs:
            print(f"Kept together as {len(set(ids[start:]))} groups (RAW + JPG + sidecars).")
        if args.by == "count" and total > start:
            print(f"Into {parts[-1] - parts[start] + 1} folders of up to {args.size} "
                  f"files each, in {dest}")
        hi = min(total, start + args.show)
        sample_dates = dates_for(start, hi)
        if hi > start:
            print(f"\nFirst {hi - start}:")
        for i in range(start, hi):
            name = names[i]
            print(f"  {name}  ->  {folder_for(i, sample_dates.get(name)) / os.path.basename(name)}")
        return 0

    # ---------------- apply ----------------
    if sys.platform == "darwin" and args.keep_awake and shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

    save_state(spath, state)
    end = total if args.limit is None else min(total, start + args.limit)
    while 0 < end < total and ids[end] == ids[end - 1]:
        end += 1                       # never stop in the middle of a pair
    print(f"Working on files {start + 1}-{end} of {total}. Ctrl+C to pause; "
          f"run the same command again to continue.")
    t0 = time.monotonic()
    done_now = bytes_now = 0
    last_report = last_save = 0
    i = start
    try:
        while i < end:
            hi = min(end, i + BLOCK)
            while hi < end and ids[hi] == ids[hi - 1]:
                hi += 1
            block_dates = dates_for(i, hi)
            while i < hi:
                j = group_end(i, hi)
                group = names[i:j]
                directory = folder_for(i, block_dates.get(group[0]))
                todo = []
                for n in group:
                    if already_there(src / n, directory / os.path.basename(n), args.verify):
                        state["already"] += 1
                    else:
                        todo.append(n)
                targets = []
                if todo:
                    try:
                        directory.mkdir(parents=True, exist_ok=True)
                        targets = unique_targets(directory, [os.path.basename(n) for n in todo],
                                                 first_dot=args.keep_pairs)
                    except OSError as e:
                        for n in todo:
                            state["failed"].append([n, str(e)])
                        print(f"  failed: {directory}: {e}", file=sys.stderr)
                for n, target in zip(todo, targets):
                    try:
                        size = transfer(src / n, target, args.copy, args.verify)
                        state["moved"] += 1
                        state["bytes"] += size
                        bytes_now += size
                    except FileNotFoundError:
                        state["missing"] += 1        # already moved or deleted
                    except OSError as e:
                        state["failed"].append([n, str(e)])
                        print(f"  failed: {n}: {e}", file=sys.stderr)
                done_now += j - i
                i = j
                state["next"] = i
                if done_now - last_save >= SAVE_EVERY:
                    save_state(spath, state)
                    last_save = done_now
                if done_now - last_report >= args.report_every:
                    last_report = done_now
                    elapsed = time.monotonic() - t0
                    rate = done_now / elapsed * 60 if elapsed else 0
                    eta = (total - i) / (done_now / elapsed) if elapsed else 0
                    print(f"{datetime.now():%H:%M}  {i}/{total}  {rate:.0f} files/min  "
                          f"{fmt_size(bytes_now / elapsed * 60) if elapsed else '-'}/min  "
                          f"about {fmt_duration(eta)} left", flush=True)
    except KeyboardInterrupt:
        save_state(spath, state)
        print(f"\nPaused after {i} of {total}. Run the same command again to continue.")
        return 130
    save_state(spath, state)

    elapsed = time.monotonic() - t0
    rate = done_now / elapsed * 60 if elapsed else 0
    print(f"\nThis session: {done_now} files, {fmt_size(bytes_now)} in "
          f"{fmt_duration(elapsed)} ({rate:.0f} files/min).")
    print(f"Overall: {state['moved']} {'copied' if args.copy else 'moved'} "
          f"({fmt_size(state['bytes'])}), {state['already']} already there, "
          f"{state['missing']} no longer there, "
          f"{len(state['failed'])} failed." + ("  Every copy was checksum-verified."
                                                 if args.verify and args.copy else ""))
    if i >= total:
        print("FINISHED.")
    else:
        print(f"{total - i} still to do. Run the same command again to continue.")
    return 0 if not state["failed"] else 1


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bigfolder",
        description="Split a huge folder into small, browsable subfolders.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="count the files by type (changes nothing)")
    s.add_argument("src", help="the folder (or memory card) to look at")
    s.add_argument("--recursive", "-r", action="store_true", help="include subfolders (e.g. DCIM/100CANON)")
    s.add_argument("--sizes", action="store_true", help="also add up the sizes (one lookup per file)")
    s.add_argument("--out", help="save the list of names to this file (reuse with split --names)")
    s.add_argument("--top", type=int, default=25, help="how many file types to show (default 25)")
    s.set_defaults(func=cmd_scan)

    d = sub.add_parser("split", help="divide the files into subfolders (preview unless --apply)")
    d.add_argument("src", help="the huge folder, or the memory card")
    d.add_argument("dest", help="where the subfolders go (may be inside src)")
    d.add_argument("--apply", action="store_true", help="really move/copy (default: preview only)")
    d.add_argument("--copy", action="store_true", help="copy instead of move (keeps the originals)")
    d.add_argument("--verify", action="store_true", help="check every copy with a SHA-256 checksum")
    d.add_argument("--by", choices=["count", "day", "month", "year"], default="count",
                   help="count = folders of --size files (default); day/month/year = by date")
    d.add_argument("--exif", action="store_true",
                   help="for day/month/year: use the date the photo was taken (needs exiftool)")
    d.add_argument("--recursive", "-r", action="store_true",
                   help="also take files from subfolders (e.g. DCIM/100CANON, 101CANON)")
    d.add_argument("--keep-pairs", action="store_true",
                   help="keep RAW + JPG + .xmp files with the same name together")
    d.add_argument("--size", type=int, default=1000, help="files per folder for --by count (default 1000)")
    d.add_argument("--prefix", default="Part", help='folder name prefix for --by count (default "Part")')
    d.add_argument("--names", help="use a saved name list (from scan --out) instead of reading the folder")
    d.add_argument("--ext", help='only these extensions, e.g. "jpg,png,tif"')
    d.add_argument("--preset", action="append", choices=sorted(PRESETS),
                   help="add a group of extensions (can be repeated)")
    d.add_argument("--exclude", action="append", metavar="REGEX",
                   help=r'skip file names matching this pattern, e.g. "\.app_" (can be repeated)')
    d.add_argument("--no-default-exclude", action="store_true",
                   help="also handle ._* / .DS_Store / Thumbs.db / desktop.ini")
    d.add_argument("--priority", help='handle these extensions first, e.g. "jpg,jpeg"')
    d.add_argument("--sort", choices=["disk", "name"], default="disk",
                   help="order: as stored on disk (default) or by name")
    d.add_argument("--limit", type=int, help="stop after this many files (handy for a speed test)")
    d.add_argument("--restart", action="store_true", help="ignore saved progress and plan again")
    d.add_argument("--show", type=int, default=15, help="examples shown in the preview (default 15)")
    d.add_argument("--report-every", type=int, default=100, help="progress line every N files")
    d.add_argument("--no-keep-awake", dest="keep_awake", action="store_false",
                   help="macOS: do not prevent sleep while working")
    d.set_defaults(func=cmd_split)
    return p


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if getattr(args, "size", 1) < 1:
        print("--size must be at least 1", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

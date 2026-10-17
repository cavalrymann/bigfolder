# bigfolder

**Slice a folder that's too big to open into small, browsable ones, by date or into parts. Safe, resumable, verified.**

- **A huge flat folder** (hundreds of thousands of loose files) → `Part 001`, `Part 002`, …
  or `2015/2015-06`, each opening instantly on any computer.
- **A camera's memory card** → photos sorted by the day they were taken, RAW + JPG kept
  together, every copy verified, the card untouched.
  See the **[photographer example](examples/photographer.md)**.

One Python file, no dependencies. Optional: [exiftool](https://exiftool.org) to sort by the date a photo was taken.

## The story

An interrupted attempt to organize my external SSD left one folder with **about 275,000
loose files**: photos mixed with thumbnails, icons and hidden helper files. My Mac couldn't
open it, and every file I moved took seconds. Sorting it the usual way would have taken
days, mostly spent on files that didn't matter.

The disk wasn't slow (tens of GB of video copied in minutes). The problem was **how many
files sat side by side in one folder**: on macOS with an exFAT drive, every file has to be
found among all the others. So the fix was a different order of work: **look first,
preview, then slice the big folder into small ones**, skipping what doesn't matter and
resuming across sessions. The same logic works for any overgrown folder, not just photos.

| Measured on a current Mac, 2 TB exFAT SSD | |
|---|---|
| Copying ~30 GB of videos (a few dozen files) | under 10 minutes |
| Moving files **out of the huge folder** | **~20–35 files per minute** |
| Moving files between normal-sized folders | thousands per minute |

## Quick start

Requires Python 3.8+. Every command only **previews** until you add `--apply`.
To try it safely on fake files first: `bash examples/demo.sh`.

```bash
git clone https://github.com/cavalrymann/bigfolder.git && cd bigfolder

# 1. Look: count what's inside, save the name list (changes nothing)
python3 bigfolder.py scan "/Volumes/Disk/Huge folder" --out names.txt

# 2. Preview where everything would go
python3 bigfolder.py split "/Volumes/Disk/Huge folder" "/Volumes/Disk/Parts" --names names.txt --preset photos

# 3. Do it
python3 bigfolder.py split "/Volumes/Disk/Huge folder" "/Volumes/Disk/Parts" --names names.txt --preset photos --apply
```

**Ctrl+C** pauses; the same command continues. Progress lines show files/min and time left.

**Memory card** (details in the [photographer example](examples/photographer.md)):

```bash
python3 bigfolder.py split /Volumes/CARD /Volumes/T7/Photos \
    --recursive --preset camera --keep-pairs --by day --exif --copy --verify --apply
```

Run it a second time: if everything is "already there", the import is complete.

**Documents** work the same way: `--preset documents --by month`.

## Options

| Option | |
|---|---|
| `--by count` / `day` / `month` / `year` | parts of `--size` files (default 1000), or date folders |
| `--exif` | use the date a photo was taken (needs exiftool) |
| `--recursive` | include subfolders, e.g. a card's `DCIM/100…` |
| `--keep-pairs` | keep `IMG_1.DNG`, `IMG_1.JPG`, `IMG_1.xmp` together |
| `--copy` / `--verify` | keep originals / check each copy with SHA-256 |
| `--preset` | `photos`, `raw`, `camera`, `documents`, `videos`, `sidecars` |
| `--ext` / `--exclude` | only these extensions / skip names matching a pattern |
| `--priority "jpg"` | handle these first |
| `--limit 200` | stop after 200 files (a quick speed test) |
| `--restart` | forget saved progress and plan again |

**Safe by default:** never overwrites (clashes become `name-1.jpg`), never deletes
originals before a verified copy, skips `._*`, `.DS_Store` and `Thumbs.db`, and recognizes
files already at the destination.

## Tips for huge exFAT folders on macOS

- Measure first with `--limit 200 --apply`.
- Restart the Mac before long sessions, keep Finder and Spotlight off the drive.
- Windows and Linux handle large exFAT folders much better; a virtual machine on the
  same Mac helps if the drive is passed to it as a USB device.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Related tools

[exiftool](https://exiftool.org), [sortphotos](https://github.com/seancummins/sortphotos)
and [Phockup](https://github.com/ivandokov/phockup) also sort photos by date. bigfolder
focuses on folders too big to open: reading names once, slicing safely, and resuming
across sessions.

## Background

- [How file systems can change in Sequoia with FSKit](https://eclecticlight.co/2024/06/26/how-file-systems-can-change-in-sequoia-with-fskit/) (Eclectic Light)
- [FSKit caching and performance](https://developer.apple.com/forums/thread/793013) (Apple Developer Forums)
- [Drive slow in Finder, quick on Windows](https://forums.sandisk.com/t/drive-contents-very-slow-to-show-in-finder-but-very-quick-on-windows-machine-5tb-portable/315882) (SanDisk forum)

## Acknowledgments

Sorting by the date a photo was taken uses [ExifTool](https://exiftool.org) by
Phil Harvey, called as a separate program when `--exif` is used.

## License

MIT, see [LICENSE](LICENSE).

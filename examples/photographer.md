# Example: importing an SD card as a photographer

> **About this example:** a walkthrough of the intended workflow. Outputs come from
> a simulated card used in the project's tests; timings are estimates.

A full card (say 100 GB of RAW + JPG) becomes day folders on your drive, while
the card itself is only read:

```
Photos/
└── 2024/
    ├── 2024-05-01/   IMG_0001.DNG  IMG_0001.JPG ...
    └── 2024-05-02/
```

- Sorted by the **date taken** (EXIF), RAW + JPG + `.xmp` kept together.
- Every copy **checksum-verified**; nothing overwritten; same card again copies nothing.

## Steps

Lock the card (the small switch on its side), then replace `/Volumes/CARD` and
`/Volumes/T7/Photos` with your card and destination.

```bash
# 1. Look (changes nothing)
python3 bigfolder.py scan /Volumes/CARD --recursive --sizes

# 2. Preview (changes nothing)
python3 bigfolder.py split /Volumes/CARD /Volumes/T7/Photos \
    --recursive --preset camera --keep-pairs --by day --exif --copy

# 3. Import
python3 bigfolder.py split /Volumes/CARD /Volumes/T7/Photos \
    --recursive --preset camera --keep-pairs --by day --exif --copy --verify --apply
```

Ctrl+C pauses; the same command continues.

**4. Confirm:** run step 3 again. It should report everything as `already there`.

**5. Free the card:** make a second backup, then format the card in the camera.

## Good to know

- **Time:** mostly the card's read speed. Roughly 20 min per 100 GB on UHS-I,
  about twice that with `--verify`. Measure yours with `--limit 200 --apply`.
- **Without exiftool:** leave out `--exif`; file dates are used instead.
- **Ricoh GR III / GR IIIx:** saves DNG + JPG and MOV videos (covered by
  `--preset camera`). It also has ~2 GB internal memory, so check the camera
  itself over USB too.

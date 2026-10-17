#!/usr/bin/env bash
# Safe demo: builds a fake huge folder and a fake camera card in ~/bigfolder-demo
# (or the folder you pass), then runs bigfolder on them. Touches nothing else.
#   Run from the project folder:  bash examples/demo.sh
set -e
cd "$(dirname "$0")/.."
DEMO="${1:-$HOME/bigfolder-demo}"
export BIGFOLDER_STATE_DIR="$DEMO/.state"
step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

rm -rf "$DEMO"
mkdir -p "$DEMO/Huge folder" "$DEMO/CARD/DCIM/100RICOH"
echo "Creating a fake huge folder (3,000 photos + 500 helper files) and a fake 3-day card..."
for i in $(seq -w 1 3000); do echo x > "$DEMO/Huge folder/IMG_$i.jpg"; done
for i in $(seq -w 1 500);  do echo x > "$DEMO/Huge folder/._IMG_$i.jpg"; done
n=0
for day in 01 02 03; do
  for k in $(seq 1 20); do
    n=$((n + 1)); f=$(printf "R%07d" "$n")
    for ext in DNG JPG; do
      echo x > "$DEMO/CARD/DCIM/100RICOH/$f.$ext"
      touch -t "202405${day}1200" "$DEMO/CARD/DCIM/100RICOH/$f.$ext"
    done
  done
done

step "1. Look at the huge folder"
python3 bigfolder.py scan "$DEMO/Huge folder" --out "$DEMO/names.txt"

step "2. Preview (changes nothing)"
python3 bigfolder.py split "$DEMO/Huge folder" "$DEMO/Parts" --names "$DEMO/names.txt" --show 3

step "3. Slice it, pausing after 1,200 files"
python3 bigfolder.py split "$DEMO/Huge folder" "$DEMO/Parts" --names "$DEMO/names.txt" --apply --limit 1200 --report-every 600

step "4. Same command again: resumes"
python3 bigfolder.py split "$DEMO/Huge folder" "$DEMO/Parts" --names "$DEMO/names.txt" --apply --report-every 1000
for d in "$DEMO/Parts"/*/; do echo "  $(ls "$d" | wc -l | tr -d ' ') files in $(basename "$d")"; done

step "5. Import the camera card by day (copy + verify)"
python3 bigfolder.py split "$DEMO/CARD" "$DEMO/Photos" -r --preset camera --keep-pairs --by day --copy --verify --apply
for d in "$DEMO/Photos"/*/*/; do echo "  $(ls "$d" | wc -l | tr -d ' ') files in Photos/2024/$(basename "$d")"; done

step "6. Import the same card again: nothing new"
python3 bigfolder.py split "$DEMO/CARD" "$DEMO/Photos" -r --preset camera --keep-pairs --by day --copy --verify --apply | tail -2

echo; echo "Done. Demo files are in $DEMO. Remove them with:  rm -rf \"$DEMO\""

#!/usr/bin/env bash
# Build the Poker Run explainer end to end: solver numbers, browser captures, video.
# Needs a Python with Pillow, numpy, imageio-ffmpeg and playwright (uses installed Chrome).
#   PY=/path/to/python bash video/make_poker_run_video.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
WORK=$(mktemp -d)

$PY video/collect_poker_run.py
# the demo seed's input and the beam search output the video describes
$PY -c "import json; d=json.load(open('video/poker_run_data.json'))['demo']; open('$WORK/in.txt','w').write(d['input']); open('$WORK/run.txt','w').write(d['output'])"
# the manual demo: the statement's example round, then a visit to the shop
printf '7\ndiscard 26 16 17 45\nplay 33 8 9 23 50\nend\nreroll\nbuy twin_engine\npaint 13 S\nnext\n' > "$WORK/manual.txt"
$PY -m poker_run check "$WORK/in.txt" "$WORK/manual.txt" --no-json
$PY video/capture_poker_run.py 7 "$WORK/manual.txt" "$WORK/manual" --slow 99 --no-replay
$PY video/capture_poker_run.py 7 "$WORK/run.txt" "$WORK/run" --slow 0
$PY video/render_poker_run.py "$WORK/manual" "$WORK/run"

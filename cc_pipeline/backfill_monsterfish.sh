#!/bin/bash
# Run the CC conversion backfill on monsterfish and bring the results home.
#
#   backfill_monsterfish.sh [push|convert|pull|all]   (default: all)
#
# monsterfish has 24 cores against this box's 4, so the full ~8500-file backfill
# takes hours there instead of days here. Its root filesystem is full, so every
# path used lives on /mnt/fishbowl - see cc_pipeline.monsterfish.env.
#
# Each phase is separately runnable and safely re-runnable:
#   push     rsync the CC FBX files up (incremental; skips what is already there)
#   convert  run convert_all.sh remotely (skips files that already have output)
#   pull     rsync the GLB + JSON pairs back into CC_OUT_DIR
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$here/cc_pipeline.env"

REMOTE="${CC_REMOTE:-monsterfish}"
REMOTE_ROOT="${CC_REMOTE_ROOT:-/mnt/fishbowl/gomer/fbxconv}"
REMOTE_JOBS="${CC_REMOTE_JOBS:-20}"

phase="${1:-all}"

push() {
    echo "== push: $CC_IN_DIR -> $REMOTE:$REMOTE_ROOT/fbx"
    rsync -a --info=progress2 --include='*.fbx' --exclude='*' \
        "$CC_IN_DIR/" "$REMOTE:$REMOTE_ROOT/fbx/"
}

convert() {
    echo "== convert: $REMOTE, $REMOTE_JOBS parallel jobs"
    ssh "$REMOTE" "cd $REMOTE_ROOT && ./convert_all.sh -j $REMOTE_JOBS"
}

pull() {
    echo "== pull: $REMOTE:$REMOTE_ROOT/out -> $CC_OUT_DIR"
    mkdir -p "$CC_OUT_DIR"
    rsync -a --info=progress2 \
        --include='*_anim.glb' --include='*_shapekeys.json' --exclude='*' \
        "$REMOTE:$REMOTE_ROOT/out/" "$CC_OUT_DIR/"
    chmod 644 "$CC_OUT_DIR"/*_anim.glb "$CC_OUT_DIR"/*_shapekeys.json 2>/dev/null
    echo "== local output: $(ls -1 "$CC_OUT_DIR"/*_anim.glb 2>/dev/null | wc -l) GLBs"
}

case "$phase" in
    push)    push ;;
    convert) convert ;;
    pull)    pull ;;
    all)     push && convert && pull ;;
    *)       echo "usage: backfill_monsterfish.sh [push|convert|pull|all]" >&2; exit 1 ;;
esac

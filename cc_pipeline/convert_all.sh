#!/bin/bash
# Convert every Character Creator FBX that does not yet have pipeline output.
#
#   convert_all.sh [-j jobs] [-n limit] [input_dir] [output_dir]
#
#     -j jobs    parallel conversions (default $CC_JOBS, else half the cores)
#     -n limit   stop after this many files (for a trial run)
#
# Resumable and idempotent: a file is skipped when both its GLB and its JSON
# already exist and are newer than the source FBX. Re-running after an
# interruption picks up exactly where it stopped.
#
# Failures are recorded in $CC_OUT_DIR/../cc_pipeline_failures.log and do not
# stop the run - one bad FBX should not cost you the whole batch.
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[ -f "$here/cc_pipeline.env" ] && . "$here/cc_pipeline.env"

CC_OUT_DIR="${CC_OUT_DIR:-/web/gebarenoverleg_media/fbx/cc_pipeline}"
CC_IN_DIR="${CC_IN_DIR:-/web/gebarenoverleg_media/fbx/CC}"
jobs="${CC_JOBS:-$(( $(nproc) / 2 ))}"
[ "$jobs" -lt 1 ] && jobs=1
limit=0

while getopts "j:n:" opt; do
    case "$opt" in
        j) jobs="$OPTARG" ;;
        n) limit="$OPTARG" ;;
        *) echo "usage: convert_all.sh [-j jobs] [-n limit] [in_dir] [out_dir]" >&2; exit 1 ;;
    esac
done
shift $((OPTIND - 1))
in_dir="${1:-$CC_IN_DIR}"
out_dir="${2:-$CC_OUT_DIR}"

mkdir -p "$out_dir"
fail_log="$(dirname "$out_dir")/cc_pipeline_failures.log"

# Start each run with a fresh log, keeping the previous one alongside. Appending
# forever meant a handful of files failing every hour looked like tens of
# thousands of distinct failures, which buried the actual fault.
[ -f "$fail_log" ] && mv -f "$fail_log" "$fail_log.prev"
: > "$fail_log"

# Build the work list: FBX files whose outputs are missing or stale.
work=$(mktemp) || exit 1
trap 'rm -f "$work"' EXIT

total=0
for fbx in "$in_dir"/*.fbx; do
    [ -e "$fbx" ] || continue
    total=$((total + 1))
    name=$(basename "$fbx" .fbx)
    glb="$out_dir/${name}_anim.glb"
    json="$out_dir/${name}_shapekeys.json"
    if [ -s "$glb" ] && [ -s "$json" ] && \
       [ "$glb" -nt "$fbx" ] && [ "$json" -nt "$fbx" ]; then
        continue
    fi
    printf '%s\n' "$fbx" >> "$work"
done

pending=$(wc -l < "$work" 2>/dev/null || echo 0)
if [ "$limit" -gt 0 ] && [ "$pending" -gt "$limit" ]; then
    head -n "$limit" "$work" > "$work.cut" && mv "$work.cut" "$work"
    pending=$limit
fi

echo "cc_pipeline: $total FBX in $in_dir, $pending to convert, $jobs parallel jobs"
[ "$pending" -eq 0 ] && { echo "cc_pipeline: nothing to do"; exit 0; }

started=$(date +%s)
# --line-buffered keeps the per-file "ok" lines interleaved cleanly under -P.
xargs -a "$work" -d '\n' -P "$jobs" -I{} \
    bash -c '"$0"/convert_one.sh "$1" "$2" || echo "FAILED $1" >> "$3"' \
    "$here" {} "$out_dir" "$fail_log"

elapsed=$(( $(date +%s) - started ))
failed=$(grep -c '^FAILED ' "$fail_log" 2>/dev/null || echo 0)
done_now=$(ls -1 "$out_dir"/*_anim.glb 2>/dev/null | wc -l)
echo "cc_pipeline: finished in ${elapsed}s - $done_now GLBs present, $failed failures logged in $fail_log"

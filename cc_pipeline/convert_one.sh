#!/bin/bash
# Convert one Character Creator FBX into a retarget-ready GLB plus a facial
# shape-key JSON sidecar, using the FBXtoGLBCompression pipeline.
#
#   convert_one.sh <input.fbx> [output_dir]
#
# Produces, in output_dir (default $CC_OUT_DIR):
#   <name>_anim.glb        armature + baked animation, 118 joints, centimetres
#   <name>_shapekeys.json  per-frame ARKit/CC morph weights at 24 fps
#
# Exit codes: 0 ok, 1 bad usage/missing input, 2 conversion step failed.
#
# Config comes from the environment so the same script runs locally and on a
# compute host; see cc_pipeline.env for the defaults.
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

CC_REPO="${CC_REPO:-/home/gomer/FBXtoGLBCompression}"
CC_BLENDER="${CC_BLENDER:-/home/gomer/blender-5.0.1-linux-x64/blender}"
# Node is installed via nvm, which only puts it on PATH for interactive shells.
# systemd services get a bare PATH, so the binary must be named explicitly or
# every node step fails with "command not found".
CC_NODE="${CC_NODE:-/home/gomer/.nvm/versions/node/v22.20.0/bin/node}"
CC_AVATAR="${CC_AVATAR:-/web/zin/PalmerPolo1024uastc.glb}"
CC_MORPH_ORDER="${CC_MORPH_ORDER:-$CC_REPO/out/palmerpolo_morph_order.txt}"
CC_OUT_DIR="${CC_OUT_DIR:-/web/gebarenoverleg_media/fbx/cc_pipeline}"

fbx="${1:-}"
out_dir="${2:-$CC_OUT_DIR}"

if [ -z "$fbx" ]; then
    echo "usage: convert_one.sh <input.fbx> [output_dir]" >&2
    exit 1
fi
if [ ! -f "$fbx" ]; then
    echo "convert_one: no such file: $fbx" >&2
    exit 1
fi

name=$(basename "$fbx" .fbx)
glb="$out_dir/${name}_anim.glb"
json="$out_dir/${name}_shapekeys.json"
# Blender's glTF exporter appends ".glb" when the output path does not already
# end in it, so the temporary name has to keep the extension.
glb_tmp="$out_dir/.${name}_anim.tmp.glb"
json_tmp="$out_dir/.${name}_shapekeys.tmp.json"
mkdir -p "$out_dir"

fail() { echo "convert_one[$name]: $1" >&2; rm -f "$glb_tmp" "$json_tmp"; exit 2; }

# Step 1 - Blender: FBX to GLB. Mesh objects are dropped; only the armature and
# its baked keyframes are exported (export_morph=False, hence the JSON sidecar).
if ! "$CC_BLENDER" --background --python "$CC_REPO/convert_animation.py" \
        -- "$fbx" "$glb_tmp" >/dev/null 2>&1; then
    fail "blender export failed"
fi
[ -s "$glb_tmp" ] || fail "blender produced no GLB"

# Step 2 - Insert the avatar's corrective root joint and move the bones from
# Blender's Y-up space into the avatar's Z-up bone space. Rewrites in place.
if ! "$CC_NODE" "$CC_REPO/fix_anim_skeleton.mjs" "$glb_tmp" "$CC_AVATAR" >/dev/null 2>&1; then
    fail "skeleton fix failed"
fi

# Step 3 - Make the body face the camera. --auto MEASURES the facing from the
# clip's own geometry and rotates only when it is backwards. Do not drop --auto:
# the rotation is its own inverse, and which way a clip comes out of
# fix_anim_skeleton depends on the source. Character Creator exports (FBX 7300)
# land backwards and need it; Unreal post-processed exports (FBX 7700) already
# face forward, and rotating them unconditionally turns them around.
# node_modules is a symlink so the script resolves the repo's deps.
[ -e "$here/node_modules" ] || ln -sfn "$CC_REPO/node_modules" "$here/node_modules"
if ! "$CC_NODE" "$here/fix_root_yaw.mjs" "$glb_tmp" --auto >/dev/null 2>&1; then
    fail "root yaw correction failed"
fi

# Step 4 - Confirm the skeleton is an exact match for the avatar. A PARTIAL
# match means the clip will not retarget cleanly, so treat it as a failure.
verdict=$("$CC_NODE" "$CC_REPO/verify_animation.mjs" "$glb_tmp" "$CC_AVATAR" 2>/dev/null \
          | grep -oE 'Compatible: .*')
case "$verdict" in
    *"YES"*) ;;
    *) fail "skeleton verify: ${verdict:-no verdict}" ;;
esac

# Step 5 - Facial shape keys to the JSON sidecar, emitted in the avatar's own
# morph target order (see cc_pipeline/README.md for why not --avatar-glb).
if ! python3 "$CC_REPO/fbx_to_shapekeys.py" \
        --fbx "$fbx" \
        --output "$json_tmp" \
        --morph-order-file "$CC_MORPH_ORDER" \
        --blender-path "$CC_BLENDER" >/dev/null 2>&1; then
    fail "shape key extraction failed"
fi
[ -s "$json_tmp" ] || fail "shape key extraction produced no JSON"

# Publish both outputs together so a reader never sees a GLB without its sidecar.
mv -f "$glb_tmp" "$glb" && mv -f "$json_tmp" "$json" || fail "could not publish outputs"

echo "convert_one[$name]: ok ($(stat -c%s "$glb") B glb, $(stat -c%s "$json") B json)"

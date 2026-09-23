# CC conversion pipeline

Converts Character Creator FBX exports into a retarget-ready GLB plus a facial
shape-key JSON sidecar, using
[FBXtoGLBCompression](https://github.com/J-Andersen-UvA/FBXtoGLBCompression).

```
/web/gebarenoverleg_media/fbx/CC/<name>.fbx
  -> /web/gebarenoverleg_media/fbx/cc_pipeline/<name>_anim.glb        (118 joints, cm)
  -> /web/gebarenoverleg_media/fbx/cc_pipeline/<name>_shapekeys.json  (24 fps morph weights)
```

Viewer: <https://signcollect.nl/zin/3DAnn3.html?glb=/gebarenoverleg_media/fbx/cc_pipeline/NAME_anim.glb>

## This runs alongside the legacy pipeline, it does not replace it

`fbx2glb-batch.service` still writes `<name>.glb` next to each FBX, `glb_matcher`
still links those into `vicon_files.glb_path`, and `3DAnn2.html` still reads them.
Nothing here touches any of that. The two outputs differ in ways that matter:

| | legacy `<name>.glb` | pipeline `<name>_anim.glb` |
|---|---|---|
| Skin | none (0 joints) | 118 joints, exact avatar match |
| Units | metres | centimetres |
| Facial animation | in the GLB | sidecar JSON |
| Viewer | `3DAnn2.html` (`POS_SCALE=100`) | `3DAnn3.html` (auto-detects) |

Swapping the legacy output for this one would break `3DAnn2.html`, which
multiplies positions by 100 and would double-scale centimetre data.

## Scripts

| Script | Purpose |
|---|---|
| `convert_one.sh <fbx> [out_dir]` | One file, all four steps. Exit 2 on failure. |
| `convert_all.sh [-j jobs] [-n limit]` | Sweep a directory; skips files that already have fresh output. |
| `backfill_monsterfish.sh [push\|convert\|pull\|all]` | Run the bulk backfill on monsterfish and pull results back. |
| `cc_pipeline.env` | Paths for this host. |
| `cc_pipeline.monsterfish.env` | Paths for the compute host. |

## The four steps

1. **Blender FBX -> GLB** (`convert_animation.py`) - armature and baked keyframes
   only; meshes are dropped and `export_morph=False`, which is why the shape keys
   need a sidecar.
2. **Skeleton fix** (`fix_anim_skeleton.mjs`) - inserts the avatar's corrective
   root joint (-90 deg X) and moves bones from Blender's Y-up into the avatar's
   Z-up bone space. 117 joints become 118.
3. **Root yaw** (`fix_root_yaw.mjs`) - turns the body to face the camera; see
   below.
4. **Verify** (`verify_animation.mjs`) - the skeleton must be an exact match for
   the avatar. A `PARTIAL` match is treated as a failure, not a warning.
5. **Shape keys** (`fbx_to_shapekeys.py`) - facial morph weights to JSON at 24 fps.

## Three things that will bite you

**Morph order.** `fbx_to_shapekeys.py --avatar-glb` cannot read
`PalmerPolo1024uastc.glb`: Blender 5's glTF importer rejects its
`KHR_texture_basisu` (KTX2) textures with *"Extension KHR_texture_basisu is not
available on this addon version"*. The order was extracted with gltf-transform
instead and lives in `palmerpolo_morph_order.txt`, passed as `--morph-order-file`.
Do not fall back to the repo's own `cc_shapekeys_minimal.txt` - it holds the same
62 names in a different order.

**Two nodes named `root`.** Pipeline GLBs contain both a scene root (identity
rotation, 0.01 scale) and the corrective joint that step 2 inserts, both named
`root`. Babylon retargets by name, so the scene root's identity rotation lands on
the avatar's corrective bone, cancels the -90 deg X and tips the character out of
frame - it looks like the avatar failed to load. `3DAnn3.html` guards against this
by never retargeting onto the `root` bone (`ROOT_BONE_NAMES`). Any other viewer
consuming these GLBs needs the same guard.

**The body faces backwards without step 3.** `fix_anim_skeleton.mjs` leaves a
converted clip about 176-177 degrees off the avatar's rest facing; the legacy
fbx2gltf output of the same capture measures about 3 degrees. `fix_root_yaw.mjs`
corrects it, and two properties of that fix matter:

- It is applied to root's three children (`pelvis`, `ik_foot_root`,
  `ik_hand_root`), **not** to the `root` joint itself. Viewers retarget by bone
  name onto the avatar's own root, so a change to the clip's root is ignored.
- It rotates about root-LOCAL Z, not world Y, because the root joint's -90 degree
  X rest rotation puts the vertical axis on Z below it.
- It is its own inverse, so a corrected GLB is stamped with
  `asset.extras.ccPipelineRootYawApplied` and a stamped file is skipped. Flags:
  `--check` reports marker state and writes nothing, `--mark-only` stamps a file
  corrected by an earlier unmarked run, `--force` rotates a marked file anyway.

  The marker only protects files processed by the current version. If you ever
  hand-correct a GLB outside the pipeline, stamp it with `--mark-only` or the
  next sweep will turn it back around.

## Automation

- **On arrival** - `sync_vicon_files.py` calls `convert_one.sh` for every new FBX
  landing in the `CC` subdirectory (`_convert_cc_pipeline`).
- **Hourly sweep** - `vicon-cc-pipeline.timer` runs `convert_all.sh -j 2` as the
  safety net for anything that failed or arrived while the sync was down. Two
  jobs only: this box has 4 cores and must keep serving the sync and monitor.
- **Bulk backfill** - `backfill_monsterfish.sh`, on 24 cores. Roughly 1 file per
  second there versus about 18 seconds per file here.

Failures are appended to `/web/gebarenoverleg_media/fbx/cc_pipeline_failures.log`
and never abort a run.

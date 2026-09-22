/**
 * Rotate a pipeline animation GLB 180 degrees about the character's vertical axis.
 *
 *   node fix_root_yaw.mjs <animation.glb> [--check]
 *
 * Why this exists
 * ---------------
 * fix_anim_skeleton.mjs leaves the body facing away from the camera: measured
 * against the avatar's rest pose, a converted clip faces ~176-177 degrees off,
 * while the legacy fbx2gltf output of the same capture measures ~3 degrees. The
 * error is systematic, so it is corrected here rather than in every viewer.
 *
 * It cannot be corrected on the "root" joint itself. Viewers retarget by bone
 * name onto the avatar's own root, whose rest rotation is the avatar's, not the
 * clip's - so a change there is simply ignored. The rotation is therefore
 * applied to root's three children (pelvis, ik_foot_root, ik_hand_root), in both
 * their rest transforms and their animation keyframes.
 *
 * The axis is root-LOCAL Z, not world Y: the root joint carries a -90 degree X
 * rest rotation that maps the CC bone space (Z-up) onto the scene (Y-up), so the
 * vertical axis below it is Z.
 *
 * Idempotency: applying this twice returns the clip to facing backwards, so the
 * GLB is stamped with a marker in asset.extras once corrected, and a marked file
 * is skipped. Flags:
 *   --check      report the root hierarchy and marker state, write nothing
 *   --mark-only  stamp the marker WITHOUT rotating - for files corrected by an
 *                earlier, unmarked run
 *   --force      rotate even if marked (you almost never want this)
 *   --auto       measure which way the body faces and rotate only if it is
 *                backwards, ignoring the marker entirely
 *
 * --auto is the repair mode. It derives the answer from the file's own geometry
 * rather than from a marker or a log, so it is safe to run over a directory in
 * any state - already correct, never corrected, or a half-finished mixture - and
 * safe to re-run after an interruption.
 */
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import draco3d from 'draco3dgltf';

const [, , glbPath, ...flags] = process.argv;
if (!glbPath) {
  console.error('Usage: node fix_root_yaw.mjs <animation.glb> [--check]');
  process.exit(1);
}
const checkOnly = flags.includes('--check');
const markOnly = flags.includes('--mark-only');
const auto = flags.includes('--auto');
const force = flags.includes('--force');

const MARKER = 'ccPipelineRootYawApplied';

// 180 degrees about local Z, as (x, y, z, w).
const YAW = [0, 0, 1, 0];

const quatMul = (a, b) => [
  a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
  a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
  a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
  a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
];

// Rotate a vector by a quaternion: v + 2 * cross(q.xyz, cross(q.xyz, v) + q.w * v)
const rotVec = (q, v) => {
  const [qx, qy, qz, qw] = q;
  const cx = qy * v[2] - qz * v[1] + qw * v[0];
  const cy = qz * v[0] - qx * v[2] + qw * v[1];
  const cz = qx * v[1] - qy * v[0] + qw * v[2];
  return [
    v[0] + 2 * (qy * cz - qz * cy),
    v[1] + 2 * (qz * cx - qx * cz),
    v[2] + 2 * (qx * cy - qy * cx),
  ];
};

const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'draco3d.decoder': await draco3d.createDecoderModule() });

const doc = await io.read(glbPath);
const root = doc.getRoot();
const skin = root.listSkins()[0];
if (!skin) {
  console.error('fix_root_yaw: no skin in ' + glbPath);
  process.exit(2);
}

const rootJoint = skin.listJoints()[0];
const children = rootJoint.listChildren();
if (!children.length) {
  console.error('fix_root_yaw: root joint has no children');
  process.exit(2);
}

const asset = root.getAsset();
const alreadyApplied = asset.extras && asset.extras[MARKER] === true;
const name = glbPath.split('/').pop();

if (checkOnly) {
  console.log(`${name}: marker=${alreadyApplied ? 'APPLIED' : 'absent'}`);
  console.log(`  root "${rootJoint.getName()}" children: ${children.map(c => c.getName()).join(', ')}`);
  for (const c of children) {
    console.log(`  ${c.getName()} restT=[${c.getTranslation().map(v => v.toFixed(3))}]`);
  }
  process.exit(0);
}

const stamp = () => {
  asset.extras = { ...(asset.extras || {}), [MARKER]: true };
};

// --- facing measurement, used by --auto -------------------------------------
// Composes world matrices for the clip's own skeleton at its first keyframe and
// derives the body's forward vector from the shoulder line. The CC avatar's
// canonical facing is -Z, so a positive Z component means the body is backwards.
const matMul = (a, b) => { const o = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) { let sum = 0;
    for (let k = 0; k < 4; k++) sum += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = sum; } return o; };
const trsMat = (t, q, sc) => { const [x, y, z, w] = q; return [
  (1 - 2 * (y * y + z * z)) * sc[0], (2 * (x * y + z * w)) * sc[0], (2 * (x * z - y * w)) * sc[0], 0,
  (2 * (x * y - z * w)) * sc[1], (1 - 2 * (x * x + z * z)) * sc[1], (2 * (y * z + x * w)) * sc[1], 0,
  (2 * (x * z + y * w)) * sc[2], (2 * (y * z - x * w)) * sc[2], (1 - 2 * (x * x + y * y)) * sc[2], 0,
  t[0], t[1], t[2], 1]; };

function measureForward() {
  const firstKey = new Map();
  for (const anim of root.listAnimations())
    for (const ch of anim.listChannels()) {
      const n = ch.getTargetNode(), path = ch.getTargetPath();
      const a = ch.getSampler().getOutput().getArray();
      const k = path === 'rotation' ? 4 : 3;
      if (!firstKey.has(n)) firstKey.set(n, {});
      firstKey.get(n)[path] = [...a.slice(0, k)];
    }

  const world = new Map();
  const walk = (node, parent) => {
    const o = firstKey.get(node) || {};
    const m = matMul(parent, trsMat(
      o.translation || node.getTranslation(),
      o.rotation || node.getRotation(),
      o.scale || node.getScale()));
    world.set(node.getName(), m);
    for (const c of node.listChildren()) walk(c, m);
  };
  walk(rootJoint, [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);

  const L = world.get('upperarm_l'), R = world.get('upperarm_r');
  if (!L || !R) return null;
  // forward = cross(shoulderLine, up)
  const rx = R[12] - L[12], ry = R[13] - L[13], rz = R[14] - L[14];
  const len = Math.hypot(rx, ry, rz) || 1;
  const fx = (ry / len) * 0 - (rz / len) * 1;
  const fz = (rx / len) * 1 - (ry / len) * 0;
  const flen = Math.hypot(fx, fz) || 1;
  return { x: fx / flen, z: fz / flen };
}

if (auto) {
  const fwd = measureForward();
  if (!fwd) {
    console.error(`fix_root_yaw: ${name} - cannot measure facing (missing upperarm bones)`);
    process.exit(2);
  }
  if (fwd.z < 0) {
    if (!alreadyApplied) { stamp(); await io.write(glbPath, doc); }
    console.log(`fix_root_yaw: ${name} - already faces forward (z=${fwd.z.toFixed(3)}), marked`);
    process.exit(0);
  }
  console.log(`fix_root_yaw: ${name} - faces backwards (z=${fwd.z.toFixed(3)}), rotating`);
  // fall through to the rotation below
}

if (markOnly) {
  if (alreadyApplied) {
    console.log(`fix_root_yaw: ${name} - already marked, nothing to do`);
    process.exit(0);
  }
  stamp();
  await io.write(glbPath, doc);
  console.log(`fix_root_yaw: ${name} - marked as corrected (not rotated)`);
  process.exit(0);
}

if (alreadyApplied && !force && !auto) {
  console.log(`fix_root_yaw: ${name} - already corrected, skipping`);
  process.exit(0);
}

const targets = new Set(children);
let restCount = 0;
let keyCount = 0;

// Rest transforms.
for (const c of children) {
  c.setTranslation(rotVec(YAW, c.getTranslation()));
  c.setRotation(quatMul(YAW, c.getRotation()));
  restCount++;
}

// Animation keyframes. Output accessors can be shared between channels, so each
// one is cloned before being rewritten - mutating a shared accessor in place
// would corrupt whichever other channel happens to reference it.
for (const anim of root.listAnimations()) {
  for (const ch of anim.listChannels()) {
    const node = ch.getTargetNode();
    if (!targets.has(node)) continue;

    const path = ch.getTargetPath();
    if (path !== 'translation' && path !== 'rotation') continue;

    const sampler = ch.getSampler();
    const accessor = sampler.getOutput().clone();
    const arr = Array.from(accessor.getArray());
    const stride = path === 'rotation' ? 4 : 3;

    for (let i = 0; i < arr.length; i += stride) {
      const v = arr.slice(i, i + stride);
      const out = path === 'rotation' ? quatMul(YAW, v) : rotVec(YAW, v);
      for (let k = 0; k < stride; k++) arr[i + k] = out[k];
      keyCount++;
    }

    accessor.setArray(new Float32Array(arr));
    sampler.setOutput(accessor);
  }
}

stamp();
await io.write(glbPath, doc);
console.log(`fix_root_yaw: ${name} - ${restCount} rest transforms, ${keyCount} keys rotated`);

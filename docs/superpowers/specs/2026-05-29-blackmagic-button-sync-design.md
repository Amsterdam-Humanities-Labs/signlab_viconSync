# Manual-sync button → trigger both syncs + live status

**Date:** 2026-05-29
**Status:** Approved, implementing

## Goal
The existing "Manual Sync → Yes, sync now" button on
`https://signcollect.nl/mocapStudio/3dOpname_test.html` should, in one click,
trigger **both** the mocap FBX/GLB sync **and** the Blackmagic camera sync
(`run_sync.bat`, delete-after-durable). The modal must then poll and display
the live status of **both** scripts so the operator can see they are actually
running.

## Topology (existing)
- Page → `triggerSync.php` (signcollect.nl) → `POST 127.0.0.1:8765/trigger`
- `sync_vicon_rsync.py` runs on the signcollect.nl server, control API on
  `127.0.0.1:8765`, and SSHes into the Vicon PC (`vicon@100.83.229.92`) to pull
  FBX/GLB into `/web/gebarenoverleg_media/fbx`.
- Blackmagic sync (`run_sync.bat` → `scripts.sync_clips`) runs on the Vicon PC
  and uploads to `S:\studioFiles\blackmagic_files\<date>` where `S:` is an
  rclone (WinFsp) mount that is **session-local to the interactive desktop** —
  not visible to SSH logins.

## Approach (B): scheduled task triggered over SSH
Run the `.bat` in the interactive desktop session (where `S:` exists) via a
Windows Scheduled Task `BlackmagicManualSync` ("run only when user is logged
on"). The server fires it with `ssh … schtasks /run /tn BlackmagicManualSync`
(fire-and-forget). No change to the `.bat`'s upload mechanism.

## Components
1. **Vicon PC — Scheduled Task `BlackmagicManualSync`**: action `run_sync.bat`,
   run only when user logged on, so it inherits the `S:` mount.
2. **`sync_clips.py` — status heartbeat (additive)**: write
   `bmcam_sync_status.json` `{state: running|idle, current_clip, seen,
   uploaded, deleted, failed, updated_at}` at cycle start, per clip, and end.
3. **`sync_vicon_rsync.py` — fire second sync**: in the trigger path,
   `ssh … schtasks /run /tn BlackmagicManualSync` (logged, non-fatal on error).
4. **`sync_vicon_rsync.py` — combined `/status`**: existing mocap `LAST_RUN`
   plus `bmcam_sync_status.json` read over SSH (short cache), returned as
   `{mocap:{…}, blackmagic:{…}}`.
5. **signcollect.nl — modal JS**: no new PHP needed — `triggerSync.php`
   already proxies both `action=trigger` and `action=status` to
   `127.0.0.1:8765`. The modal polls `triggerSync.php?action=status` every ~3s,
   shows two status lines (HTML-escaped), stops once both report a fresh idle
   (with a startup grace period and a ~10-min cap).
6. **Vicon PC — `run_sync_task.cmd` wrapper**: the scheduled task runs this
   wrapper, which calls `run_sync.bat < nul` so the `.bat`'s `pause` calls
   return immediately when launched non-interactively (double-click behaviour
   is unchanged).

## Implementation status (2026-05-29)
Built and verified: scheduled task created (Ready/Interactive); probe confirmed
the task sees `S:`; `sync_clips.py` heartbeat added (compiles; `_write_status`
unit-tested); `sync_vicon_rsync.py` trigger + combined `/status` (compiles);
modal JS (node --check passes). **Remaining:** restart
`vicon-sync-rsync.service` to deploy the control-API changes (needs operator
authorization).

## Error handling
- `schtasks /run` SSH failure → modal shows "Blackmagic trigger failed"; mocap
  sync still proceeds.
- `bmcam_sync_status.json` missing/stale (> a few min) → Blackmagic status
  "unknown / not running".
- Status polling has a max duration cap.

## Load-bearing assumption to verify first
A "run only when user logged on" scheduled task must actually see the
interactive `S:` mount. Verify with a throwaway probe task before wiring the
button. If it fails, switch to Approach A (rclone-direct upload, no mount).

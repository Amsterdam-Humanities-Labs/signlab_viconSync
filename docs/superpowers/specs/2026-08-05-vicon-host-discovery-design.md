# Vicon PC host discovery via Tailscale

**Date:** 2026-08-05
**Status:** Approved, pending implementation

## Problem

`sync_vicon_rsync.py` and `ftp_monitor.py` both address the Vicon PC by a
hardcoded Tailscale IP, `100.83.229.92`. That address belonged to tailnet node
`vicon-sb001869`, which went offline on 2026-06-29. The PC has since rejoined
under a new node identity, `vicon-sb001869-1`, at `100.111.64.24`.

The two peers share the Windows `HostName` `VICON-SB001869`. Only `DNSName`
distinguishes them:

| DNSName | IPv4 | Online | LastSeen |
|---|---|---|---|
| `vicon-sb001869` | 100.83.229.92 | false | 2026-06-29T18:20:42Z |
| `vicon-sb001869-1` | 100.111.64.24 | true | — |

Rejoining is not a one-off: the next reinstall produces `-2`, and so on. Any
fix that pins an address or a specific node name fails again on the next
rejoin.

### The failure was silent

Worse than the outage itself is that nothing reported it. `list_date_directories`
(`sync_vicon_rsync.py:428`) returns `[]` both when the SSH call fails and when a
directory is genuinely empty. The caller at `:829` cannot distinguish the two,
logs a WARNING, and continues without incrementing `stats['errors']`. With
`errors == 0`, `:1196` sets `status = "success"` and heartbeats green.

The last cycle that transferred anything ran on 2026-06-15. Every daily cycle
since — roughly seven weeks — reported success while downloading zero files.

## Goals

1. Both daemons find the Vicon PC by scanning the tailnet, not by a fixed address.
2. An unreachable Vicon PC produces a loud, accurate error, never a green heartbeat.
3. A reachable-but-unreadable Vicon PC also stops reporting success.
4. No hardcoded `100.x` address remains in the repository.

## Non-goals

- Porting the sync script's SSH calls to argv lists with `ConnectTimeout` /
  `BatchMode`, and removing the `shell=True` password interpolation at
  `sync_vicon_rsync.py:314`. Worth doing; tracked separately.
- Moving the plaintext credentials out of `sync_vicon_rsync.py:36` and
  `monitor_config.json`.

## Design

### New module: `vicon_host.py`

```python
class ViconOffline(Exception):
    """No reachable vicon* peer in the tailnet."""

def resolve_vicon_host(prefix="vicon", probe_port=22, timeout=10) -> tuple[str, str]:
    """Return (ipv4, dns_name) for the live Vicon PC. Raises ViconOffline."""
```

**Candidate matching.** Run `tailscale status --json`. Keep any peer whose
`DNSName` label (text before the first dot) or `HostName` starts with `prefix`,
case-insensitively. Both fields are checked because the duplicate peers share a
`HostName`; `DNSName` is the unique identifier and is what appears in logs.

**Selection order:**

| Situation | Result |
|---|---|
| No `vicon*` peer at all | `ViconOffline("no vicon* peer in tailnet")` |
| Candidates exist, none `Online` | `ViconOffline` listing each name and `LastSeen` |
| Exactly one online, probe succeeds | return it |
| Exactly one online, probe fails | `ViconOffline("<name> online but port N closed")` |
| Several online | probe in descending `DNSName` order, return the first that answers |

`LastSeen` cannot break ties among online peers — Tailscale reports
`0001-01-01T00:00:00Z` for them. A TCP probe is used instead, which has the
useful side effect of confirming the service is actually up.

Descending `DNSName` order means the highest rejoin suffix is tried first
(`vicon-sb001869-2` before `-1` before the bare name), so when a stale node is
briefly online alongside its replacement, the newest wins.

`timeout` bounds each external operation separately: it is passed to the
`tailscale status --json` subprocess, and used again as the connect timeout for
each TCP probe. Worst case with N online candidates is `timeout * (N + 1)`.

**IPv4 selection.** Take the first entry in `TailscaleIPs` containing a `.`.
Never the `fd7a:` IPv6 address.

**`probe_port` is the caller's choice.** The sync script passes 22, the FTP
monitor passes 21, so each validates the port it will actually use rather than
trusting a generic liveness flag.

**Caching.** A module-level `{"ts", "value"}` memo with a 30-second TTL,
mirroring the `_BM_STATUS_CACHE` pattern at `sync_vicon_rsync.py:98`. Successes
are cached; failures always re-probe. This stops the control API from shelling
out to `tailscale` on every `/status` request, while leaving the daily sync
cycle effectively uncached.

### Wiring: `sync_vicon_rsync.py`

- Delete the `SSH_HOST` constant at `:34`.
- `ViconSync.__init__` accepts `host`; `ssh_execute` uses `self.host`.
- Each iteration of the main loop resolves before doing anything else. On
  `ViconOffline`: log ERROR, heartbeat `status="error"` with the offline detail,
  set `LAST_RUN["state"] = "offline"`, and sleep to the next cycle. Under
  `--once`, exit 1. No SSH is attempted, so the two 60-second timeouts that
  currently pad every failed cycle disappear.
- `_control_ssh` (`:964`) resolves through the same cached call, so `/trigger`
  and the Blackmagic status read follow the PC automatically.
- `GET /status` gains a `"vicon"` block: `{"host", "dns_name", "online"}`.

### Wiring: `ftp_monitor.py`

- `FtpConnectionManager.connect()` resolves instead of reading
  `config['ftp']['host']`, and returns `False` on `ViconOffline` so the existing
  five-retry backoff at `:193-212` handles it unchanged.
- In `monitor_config.json`, replace `"host": "100.83.229.92"` with
  `"host_prefix": "vicon"`.

### Error accounting

`list_date_directories` returns `None` on SSH failure and `[]` on a genuinely
empty directory. The caller splits the two:

```python
all_date_dirs = self.list_date_directories(base_path)
if all_date_dirs is None:
    logger.error(f"Failed to list {base_path} — treating as sync error")
    self.stats['errors'] += 1
    continue
if not all_date_dirs:
    logger.warning(f"No date directories in {base_path}")
    continue
```

`stats['errors'] > 0` already drives `status = "warning"` at `:1196`, so no
heartbeat logic changes.

## Testing

`tests/test_vicon_host.py`, hermetic in the style of commit `070c4dc` —
`_tailscale_status` and `_probe` are both mocked, no network access. The fixture
is the real tailnet payload above: offline `vicon-sb001869` and online
`vicon-sb001869-1`, sharing a `HostName`.

Cases:

1. Picks the online peer, returns `100.111.64.24`.
2. Ignores the stale offline duplicate despite the identical `HostName`.
3. All candidates offline: raises, message includes each `LastSeen`.
4. No `vicon*` peer at all: raises.
5. Several online: probe order decides.
6. Returns IPv4, not the `fd7a:` address.
7. `tailscale` binary missing: raises `ViconOffline`.
8. `tailscale` exits non-zero: raises `ViconOffline`.
9. Malformed JSON: raises `ViconOffline`.

The error-accounting change gets its own new file, `tests/test_sync_vicon_rsync.py`
(the repo currently has tests only for `compress_blackmagic.py`), covering:
`list_date_directories` returns `None` on `rc != 0` and `[]` on empty output,
and the caller increments `stats['errors']` in the former case but not the
latter.

## Verification

Implementation is verified against the live tailnet, where the Vicon PC is
currently reachable at `100.111.64.24` on both port 22 and port 21:

- `resolve_vicon_host(probe_port=22)` returns `("100.111.64.24", "vicon-sb001869-1")`.
- `sync_vicon_rsync.py --once --dry-run` completes against the discovered host.
- `curl localhost:8765/status` reports the `vicon` block with `online: true`.

# Vicon Host Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both Vicon daemons locate the Vicon PC by scanning the tailnet at runtime instead of using a hardcoded IP, and an unreachable or unreadable Vicon PC produces a loud error instead of a green heartbeat.

**Architecture:** A new `vicon_host.py` shells out to `tailscale status --json`, matches peers whose DNS label or hostname starts with `vicon`, discards stale offline duplicates, and TCP-probes the survivors on a caller-chosen port. `sync_vicon_rsync.py` and `ftp_monitor.py` call it where they previously read a fixed address. Separately, `list_date_directories` gains a `None` return so a failed listing stops looking like an empty directory.

**Tech Stack:** Python 3.12, stdlib only (`subprocess`, `socket`, `json`), pytest for tests. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-vicon-host-discovery-design.md`.
- Tests run from the repo root as `python3 -m pytest tests/ -q`. There is no pytest config file; the tests import project modules directly (`import vicon_host`), which only resolves because `python3 -m` puts the CWD on `sys.path`. Bare `pytest` will fail with `ModuleNotFoundError`.
- All tests are hermetic — no network, no `tailscale` subprocess, no FTP. Mock `subprocess.run`, `socket.create_connection`, and `FTP`. This matches commit `070c4dc`, which made `preflight_ssh` hermetic for the same reason.
- The prefix is `vicon`, matched case-insensitively. Real peers report `HostName` as uppercase `VICON-SB001869`.
- No hardcoded `100.x` address may remain in `sync_vicon_rsync.py`, `ftp_monitor.py`, or `monitor_config.json` when the plan is complete.
- Out of scope, do not attempt: porting SSH calls to argv lists, adding `ConnectTimeout`/`BatchMode`, removing the `shell=True` password interpolation at `sync_vicon_rsync.py:314`, or moving credentials out of source. These are listed as non-goals in the spec.
- Commit after every task. Work happens on branch `vicon-host-discovery`.

---

### Task 1: `ViconOffline` and the tailscale status reader

**Files:**
- Create: `vicon_host.py`
- Test: `tests/test_vicon_host.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ViconOffline(Exception)`; `_tailscale_status(timeout=10) -> dict`; module constants `TAILSCALE_BIN = "tailscale"`, `DEFAULT_PREFIX = "vicon"`, `CACHE_TTL_SECONDS = 30`.

Every failure mode collapses to `ViconOffline` because callers respond to all of them identically: skip the cycle and report an error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vicon_host.py`:

```python
import json
import subprocess

import pytest

import vicon_host


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_tailscale_status_parses_json(monkeypatch):
    monkeypatch.setattr(
        vicon_host.subprocess, "run",
        lambda *a, **k: FakeCompleted(stdout='{"Peer": {}}'),
    )
    assert vicon_host._tailscale_status() == {"Peer": {}}


def test_tailscale_status_raises_when_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("tailscale")
    monkeypatch.setattr(vicon_host.subprocess, "run", boom)
    with pytest.raises(vicon_host.ViconOffline, match="not found on PATH"):
        vicon_host._tailscale_status()


def test_tailscale_status_raises_on_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=10)
    monkeypatch.setattr(vicon_host.subprocess, "run", boom)
    with pytest.raises(vicon_host.ViconOffline, match="timed out"):
        vicon_host._tailscale_status()


def test_tailscale_status_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        vicon_host.subprocess, "run",
        lambda *a, **k: FakeCompleted(returncode=1, stderr="not logged in"),
    )
    with pytest.raises(vicon_host.ViconOffline, match="not logged in"):
        vicon_host._tailscale_status()


def test_tailscale_status_raises_on_malformed_json(monkeypatch):
    monkeypatch.setattr(
        vicon_host.subprocess, "run",
        lambda *a, **k: FakeCompleted(stdout="not json at all"),
    )
    with pytest.raises(vicon_host.ViconOffline, match="unparseable"):
        vicon_host._tailscale_status()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vicon_host'`

- [ ] **Step 3: Write the module**

Create `vicon_host.py`:

```python
"""Resolve the Vicon PC's current address from the Tailscale node list.

The Vicon PC rejoins the tailnet under a new node identity after a reinstall
(vicon-sb001869 -> vicon-sb001869-1 -> ...). Each rejoin assigns a fresh 100.x
address and leaves the previous node behind as a permanently-offline peer
carrying the *same* Windows HostName. Pinning an address or a full node name
therefore breaks on the next rejoin, so we scan for the node instead.
"""

import json
import socket
import subprocess
import time

TAILSCALE_BIN = "tailscale"
DEFAULT_PREFIX = "vicon"
CACHE_TTL_SECONDS = 30


class ViconOffline(Exception):
    """No reachable vicon* peer in the tailnet."""


def _tailscale_status(timeout=10):
    """Return the parsed `tailscale status --json` document.

    A missing CLI, a non-zero exit, a timeout and unparseable output all raise
    ViconOffline: to every caller they mean the same thing, which is that we
    cannot locate the Vicon PC right now.
    """
    argv = [TAILSCALE_BIN, "status", "--json"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ViconOffline(f"{TAILSCALE_BIN} not found on PATH")
    except subprocess.TimeoutExpired:
        raise ViconOffline(f"{TAILSCALE_BIN} status timed out after {timeout}s")
    if r.returncode != 0:
        raise ViconOffline(f"{TAILSCALE_BIN} status failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except ValueError as exc:
        raise ViconOffline(f"{TAILSCALE_BIN} status returned unparseable JSON: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add vicon_host.py tests/test_vicon_host.py
git commit -m "Add vicon_host.ViconOffline and tailscale status reader"
```

---

### Task 2: Candidate matching and IPv4 extraction

**Files:**
- Modify: `vicon_host.py`
- Test: `tests/test_vicon_host.py`

**Interfaces:**
- Consumes: `DEFAULT_PREFIX` from Task 1.
- Produces: `_label(peer) -> str`; `_matches(peer, prefix) -> bool`; `_candidates(status, prefix=DEFAULT_PREFIX) -> list[dict]` sorted newest-first; `_ipv4_of(peer) -> str | None`.

The two real peers share `HostName`, so `DNSName` is the only distinguishing field and is what gets logged. Sorting descending by label puts the highest rejoin suffix first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vicon_host.py`:

```python
PEER_OFFLINE = {
    "HostName": "VICON-SB001869",
    "DNSName": "vicon-sb001869.taila8bdbd.ts.net.",
    "TailscaleIPs": ["100.83.229.92", "fd7a:115c:a1e0::a535:e55c"],
    "OS": "windows",
    "Online": False,
    "LastSeen": "2026-06-29T18:20:42.1Z",
}

PEER_ONLINE = {
    "HostName": "VICON-SB001869",
    "DNSName": "vicon-sb001869-1.taila8bdbd.ts.net.",
    "TailscaleIPs": ["100.111.64.24", "fd7a:115c:a1e0::d234:4019"],
    "OS": "windows",
    "Online": True,
    "LastSeen": "0001-01-01T00:00:00Z",
}

PEER_PI = {
    "HostName": "raspberrypi",
    "DNSName": "raspberrypi.taila8bdbd.ts.net.",
    "TailscaleIPs": ["100.111.12.119"],
    "Online": True,
    "LastSeen": "0001-01-01T00:00:00Z",
}


def make_status(*peers):
    return {"Peer": {p["DNSName"]: p for p in peers}}


def test_label_strips_domain_and_trailing_dot():
    assert vicon_host._label(PEER_ONLINE) == "vicon-sb001869-1"


def test_candidates_match_vicon_and_exclude_others():
    status = make_status(PEER_OFFLINE, PEER_ONLINE, PEER_PI)
    labels = [vicon_host._label(p) for p in vicon_host._candidates(status)]
    assert labels == ["vicon-sb001869-1", "vicon-sb001869"]


def test_candidates_sorted_newest_rejoin_first():
    newer = dict(PEER_ONLINE, DNSName="vicon-sb001869-2.taila8bdbd.ts.net.")
    status = make_status(PEER_OFFLINE, PEER_ONLINE, newer)
    labels = [vicon_host._label(p) for p in vicon_host._candidates(status)]
    assert labels[0] == "vicon-sb001869-2"


def test_candidates_empty_when_no_vicon_peer():
    assert vicon_host._candidates(make_status(PEER_PI)) == []


def test_candidates_handles_missing_peer_key():
    assert vicon_host._candidates({}) == []


def test_matches_is_case_insensitive():
    peer = {"HostName": "VICON-SB001869", "DNSName": ""}
    assert vicon_host._matches(peer, "vicon") is True


def test_ipv4_of_skips_ipv6():
    assert vicon_host._ipv4_of(PEER_ONLINE) == "100.111.64.24"


def test_ipv4_of_returns_none_without_v4():
    assert vicon_host._ipv4_of({"TailscaleIPs": ["fd7a:115c:a1e0::1"]}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: FAIL — `AttributeError: module 'vicon_host' has no attribute '_label'`

- [ ] **Step 3: Write the implementation**

Append to `vicon_host.py`:

```python
def _label(peer):
    """The peer's unique short name.

    'vicon-sb001869-1.taila8bdbd.ts.net.' -> 'vicon-sb001869-1'. Falls back to
    HostName, which is NOT unique - both Vicon nodes report VICON-SB001869.
    """
    dns = (peer.get("DNSName") or "").strip(".")
    if dns:
        return dns.split(".")[0]
    return (peer.get("HostName") or "").strip()


def _matches(peer, prefix):
    p = prefix.lower()
    return _label(peer).lower().startswith(p) or (peer.get("HostName") or "").lower().startswith(p)


def _candidates(status, prefix=DEFAULT_PREFIX):
    """Every vicon* peer, newest rejoin suffix first.

    Descending label order puts vicon-sb001869-2 before -1 before the bare
    name, so when a stale node is briefly online alongside its replacement the
    newest is tried first. The comparison is lexicographic, which would order
    -10 before -2; ten rejoins is not a case worth complicating this for.
    """
    peers = (status or {}).get("Peer") or {}
    matched = [p for p in peers.values() if _matches(p, prefix)]
    return sorted(matched, key=_label, reverse=True)


def _ipv4_of(peer):
    """The 100.x address. TailscaleIPs is [v4, v6]; never return the fd7a: one."""
    for addr in peer.get("TailscaleIPs") or []:
        if "." in addr:
            return addr
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
git add vicon_host.py tests/test_vicon_host.py
git commit -m "Add vicon peer matching and IPv4 extraction"
```

---

### Task 3: Probe and `resolve_vicon_host`

**Files:**
- Modify: `vicon_host.py`
- Test: `tests/test_vicon_host.py`

**Interfaces:**
- Consumes: `_tailscale_status`, `_candidates`, `_ipv4_of`, `_label`, `ViconOffline`.
- Produces: `_probe(ip, port, timeout) -> bool`; `resolve_vicon_host(prefix=DEFAULT_PREFIX, probe_port=22, timeout=10) -> tuple[str, str]` returning `(ipv4, label)`.

`probe_port` is the caller's choice so each daemon validates the port it will actually use: 22 for the sync script, 21 for the FTP monitor.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vicon_host.py`:

```python
def patch_status(monkeypatch, status):
    monkeypatch.setattr(vicon_host, "_tailscale_status", lambda timeout=10: status)


def patch_probe(monkeypatch, reachable):
    """reachable: set of IPs that accept connections."""
    monkeypatch.setattr(
        vicon_host, "_probe",
        lambda ip, port, timeout: ip in reachable,
    )


def test_resolve_returns_online_peer(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_OFFLINE, PEER_ONLINE, PEER_PI))
    patch_probe(monkeypatch, {"100.111.64.24"})
    assert vicon_host.resolve_vicon_host() == ("100.111.64.24", "vicon-sb001869-1")


def test_resolve_ignores_stale_duplicate_hostname(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_OFFLINE, PEER_ONLINE))
    patch_probe(monkeypatch, {"100.111.64.24", "100.83.229.92"})
    ip, _ = vicon_host.resolve_vicon_host()
    assert ip != "100.83.229.92"


def test_resolve_raises_when_no_vicon_peer(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_PI))
    with pytest.raises(vicon_host.ViconOffline, match="no vicon\\* peer in tailnet"):
        vicon_host.resolve_vicon_host()


def test_resolve_raises_when_all_offline_and_reports_last_seen(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_OFFLINE))
    with pytest.raises(vicon_host.ViconOffline) as exc:
        vicon_host.resolve_vicon_host()
    assert "vicon-sb001869" in str(exc.value)
    assert "2026-06-29" in str(exc.value)


def test_resolve_raises_when_online_but_port_closed(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_ONLINE))
    patch_probe(monkeypatch, set())
    with pytest.raises(vicon_host.ViconOffline, match="port 22 closed"):
        vicon_host.resolve_vicon_host()


def test_resolve_probe_breaks_tie_between_online_peers(monkeypatch):
    newer = dict(PEER_ONLINE, DNSName="vicon-sb001869-2.taila8bdbd.ts.net.",
                 TailscaleIPs=["100.99.99.99"])
    patch_status(monkeypatch, make_status(PEER_ONLINE, newer))
    patch_probe(monkeypatch, {"100.111.64.24"})
    assert vicon_host.resolve_vicon_host() == ("100.111.64.24", "vicon-sb001869-1")


def test_resolve_passes_probe_port_through(monkeypatch):
    patch_status(monkeypatch, make_status(PEER_ONLINE))
    seen = {}

    def fake_probe(ip, port, timeout):
        seen["port"] = port
        return True

    monkeypatch.setattr(vicon_host, "_probe", fake_probe)
    vicon_host.resolve_vicon_host(probe_port=21)
    assert seen["port"] == 21


def test_probe_returns_false_on_refused(monkeypatch):
    def boom(addr, timeout=None):
        raise OSError("refused")
    monkeypatch.setattr(vicon_host.socket, "create_connection", boom)
    assert vicon_host._probe("100.0.0.1", 22, 1) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: FAIL — `AttributeError: module 'vicon_host' has no attribute 'resolve_vicon_host'`

- [ ] **Step 3: Write the implementation**

Append to `vicon_host.py`:

```python
def _probe(ip, port, timeout):
    """True if a TCP connect to ip:port succeeds within timeout."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _offline_detail(candidates):
    return ", ".join(
        f"{_label(p)} (last seen {p.get('LastSeen') or 'never'})" for p in candidates
    ) or "none"


def resolve_vicon_host(prefix=DEFAULT_PREFIX, probe_port=22, timeout=10):
    """Return (ipv4, label) for the live Vicon PC.

    timeout bounds each external operation separately: the tailscale
    subprocess, then each TCP probe. Worst case with N online candidates is
    timeout * (N + 1). Raises ViconOffline if nothing answers.
    """
    candidates = _candidates(_tailscale_status(timeout=timeout), prefix)
    if not candidates:
        raise ViconOffline(f"no {prefix}* peer in tailnet")

    online = [p for p in candidates if p.get("Online")]
    if not online:
        raise ViconOffline(
            f"no {prefix}* peer online; known: {_offline_detail(candidates)}"
        )

    tried = []
    for peer in online:
        ip = _ipv4_of(peer)
        if not ip:
            continue
        if _probe(ip, probe_port, timeout):
            return ip, _label(peer)
        tried.append(f"{_label(peer)} ({ip})")

    raise ViconOffline(
        f"{prefix}* peer(s) online but port {probe_port} closed: "
        f"{', '.join(tried) or 'no IPv4 address'}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: PASS, 21 passed

- [ ] **Step 5: Commit**

```bash
git add vicon_host.py tests/test_vicon_host.py
git commit -m "Add resolve_vicon_host with TCP probe selection"
```

---

### Task 4: TTL cache for hot callers

**Files:**
- Modify: `vicon_host.py`
- Test: `tests/test_vicon_host.py`

**Interfaces:**
- Consumes: `resolve_vicon_host`, `CACHE_TTL_SECONDS`.
- Produces: `resolve_vicon_host_cached(prefix=DEFAULT_PREFIX, probe_port=22, timeout=10, ttl=CACHE_TTL_SECONDS) -> tuple[str, str]`; `_clear_cache() -> None` for tests.

The control API hits this on every `/status` request; the daily sync loop is effectively uncached at a 30-second TTL. Only successes are cached, so an offline PC is never reported live for longer than one call. The cache is keyed on `(prefix, probe_port)` so a process using two ports cannot get a cross-port hit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vicon_host.py`:

```python
def test_cached_reuses_result_within_ttl(monkeypatch):
    vicon_host._clear_cache()
    calls = []
    monkeypatch.setattr(
        vicon_host, "resolve_vicon_host",
        lambda **kw: calls.append(1) or ("100.111.64.24", "vicon-sb001869-1"),
    )
    vicon_host.resolve_vicon_host_cached()
    vicon_host.resolve_vicon_host_cached()
    assert len(calls) == 1


def test_cached_refreshes_after_ttl(monkeypatch):
    vicon_host._clear_cache()
    calls = []
    monkeypatch.setattr(
        vicon_host, "resolve_vicon_host",
        lambda **kw: calls.append(1) or ("100.111.64.24", "vicon-sb001869-1"),
    )
    vicon_host.resolve_vicon_host_cached(ttl=0)
    vicon_host.resolve_vicon_host_cached(ttl=0)
    assert len(calls) == 2


def test_cached_does_not_cache_failures(monkeypatch):
    vicon_host._clear_cache()
    calls = []

    def boom(**kw):
        calls.append(1)
        raise vicon_host.ViconOffline("offline")

    monkeypatch.setattr(vicon_host, "resolve_vicon_host", boom)
    for _ in range(2):
        with pytest.raises(vicon_host.ViconOffline):
            vicon_host.resolve_vicon_host_cached()
    assert len(calls) == 2


def test_cached_keys_on_probe_port(monkeypatch):
    vicon_host._clear_cache()
    ports = []
    monkeypatch.setattr(
        vicon_host, "resolve_vicon_host",
        lambda **kw: ports.append(kw["probe_port"]) or ("100.111.64.24", "n"),
    )
    vicon_host.resolve_vicon_host_cached(probe_port=22)
    vicon_host.resolve_vicon_host_cached(probe_port=21)
    assert ports == [22, 21]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: FAIL — `AttributeError: module 'vicon_host' has no attribute '_clear_cache'`

- [ ] **Step 3: Write the implementation**

Append to `vicon_host.py`:

```python
_CACHE = {}  # (prefix, probe_port) -> (timestamp, (ip, label))


def resolve_vicon_host_cached(prefix=DEFAULT_PREFIX, probe_port=22,
                              timeout=10, ttl=CACHE_TTL_SECONDS):
    """resolve_vicon_host with a short TTL memo, for callers hit frequently.

    Only successes are cached: a failure always re-probes, so an offline PC is
    never reported as live for longer than a single call.
    """
    key = (prefix, probe_port)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = resolve_vicon_host(prefix=prefix, probe_port=probe_port, timeout=timeout)
    _CACHE[key] = (now, value)
    return value


def _clear_cache():
    _CACHE.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_vicon_host.py -q`
Expected: PASS, 25 passed

- [ ] **Step 5: Commit**

```bash
git add vicon_host.py tests/test_vicon_host.py
git commit -m "Add TTL cache for repeated host resolution"
```

---

### Task 5: Error accounting in the sync script

**Files:**
- Modify: `sync_vicon_rsync.py:428-447` (`list_date_directories`), `sync_vicon_rsync.py:827-831` (caller)
- Test: `tests/test_sync_vicon_rsync.py` (new file)

**Interfaces:**
- Consumes: nothing from earlier tasks — this is independent of discovery and can be reviewed on its own.
- Produces: `list_date_directories` now returns `None` on SSH failure and `[]` on a genuinely empty directory.

This is the fix for the seven-week silent failure. Today both cases return `[]`, the caller logs a WARNING, and `stats['errors']` stays 0, so `:1196` reports success.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_vicon_rsync.py`:

```python
import sync_vicon_rsync as svr


def make_syncer(tmp_path):
    cache = svr.SyncCache(str(tmp_path / "cache.json"))
    return svr.ViconSync(dry_run=True, cache=cache)


def test_list_date_directories_returns_none_on_ssh_failure(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 1))
    assert syncer.list_date_directories("E:\\Recordings") is None


def test_list_date_directories_returns_empty_list_when_no_dirs(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: ("", 0))
    assert syncer.list_date_directories("E:\\Recordings") == []


def test_list_date_directories_parses_and_sorts_dates(tmp_path, monkeypatch):
    syncer = make_syncer(tmp_path)
    out = "2026-06-15\n2026-05-11\nnot-a-date\n"
    monkeypatch.setattr(syncer, "ssh_execute", lambda cmd, timeout=60: (out, 0))
    assert syncer.list_date_directories("E:\\Recordings") == ["2026-05-11", "2026-06-15"]
```

Note: `ViconSync.__init__` gains a required `host` argument in Task 6. When Task 6 lands, update `make_syncer` to `svr.ViconSync(host="100.0.0.1", dry_run=True, cache=cache)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sync_vicon_rsync.py -q`
Expected: FAIL on the first test — `assert [] is None`

- [ ] **Step 3: Change the return contract**

In `sync_vicon_rsync.py`, in `list_date_directories`, replace:

```python
        if returncode != 0:
            logger.error(f"Failed to list date directories in {base_path}")
            return []
```

with:

```python
        if returncode != 0:
            # None means "the call failed", distinct from [] meaning "the
            # directory is genuinely empty". The caller counts only the former
            # as a sync error.
            logger.error(f"Failed to list date directories in {base_path}")
            return None
```

- [ ] **Step 4: Update the caller**

In `sync_vicon_rsync.py`, in `run()`, replace:

```python
            all_date_dirs = self.list_date_directories(base_path)

            if not all_date_dirs:
                logger.warning(f"No date directories found in {base_path}")
                continue
```

with:

```python
            all_date_dirs = self.list_date_directories(base_path)

            if all_date_dirs is None:
                logger.error(f"Failed to list {base_path} — treating as sync error")
                self.stats['errors'] += 1
                continue

            if not all_date_dirs:
                logger.warning(f"No date directories found in {base_path}")
                continue
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, 50 passed — 22 pre-existing from `test_compress_blackmagic.py`, 25 from `test_vicon_host.py`, 3 new here

- [ ] **Step 6: Commit**

```bash
git add sync_vicon_rsync.py tests/test_sync_vicon_rsync.py
git commit -m "Distinguish failed directory listing from empty one

A failed listing returned [] exactly like an empty directory, so the
caller logged a warning without incrementing stats['errors'] and the
cycle heartbeated success. Every daily sync since 2026-06-15 reported
success while downloading nothing."
```

---

### Task 6: Wire discovery into the sync script

**Files:**
- Modify: `sync_vicon_rsync.py` — line 34 (`SSH_HOST`), `ViconSync.__init__`, `ssh_execute`, `run()` log line, `--help` text, `_control_ssh`, `ControlHandler.do_GET`, `main()` loop
- Test: `tests/test_sync_vicon_rsync.py`

**Interfaces:**
- Consumes: `vicon_host.resolve_vicon_host`, `vicon_host.resolve_vicon_host_cached`, `vicon_host.ViconOffline`.
- Produces: `ViconSync(host, dry_run=False, cache=None)` — `host` is now the first positional parameter.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_vicon_rsync.py`:

```python
import vicon_host


def test_syncer_uses_injected_host_in_ssh_command(tmp_path, monkeypatch):
    cache = svr.SyncCache(str(tmp_path / "cache.json"))
    syncer = svr.ViconSync(host="100.111.64.24", dry_run=True, cache=cache)
    seen = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(svr.subprocess, "run", fake_run)
    syncer.ssh_execute("dir")
    assert "100.111.64.24" in seen["cmd"]


def test_control_ssh_returns_error_when_vicon_offline(monkeypatch):
    def boom(**kwargs):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(svr.vicon_host, "resolve_vicon_host_cached", boom)
    assert svr._control_ssh("schtasks /run /tn X") == ("", 1)


def test_module_has_no_hardcoded_vicon_ip():
    source = open("sync_vicon_rsync.py").read()
    assert "100.83.229.92" not in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sync_vicon_rsync.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'host'`

- [ ] **Step 3: Update the module header and the class**

In `sync_vicon_rsync.py`, add to the imports block:

```python
import vicon_host
```

Delete this line entirely (line 34):

```python
SSH_HOST = "100.83.229.92"
```

Change `ViconSync.__init__` from:

```python
    def __init__(self, dry_run=False, cache: Optional[SyncCache] = None):
        self.dry_run = dry_run
```

to:

```python
    def __init__(self, host, dry_run=False, cache: Optional[SyncCache] = None):
        self.host = host
        self.dry_run = dry_run
```

In `ssh_execute`, change the `full_cmd` line from `{SSH_USER}@{SSH_HOST}` to `{SSH_USER}@{self.host}`:

```python
        full_cmd = f'sshpass -p {SSH_PASS} ssh -o StrictHostKeyChecking=no {SSH_USER}@{self.host} "{escaped_command}"'
```

In `run()`, change the startup log line to use the injected host:

```python
        logger.info(f"Starting Vicon file sync from {self.host}")
```

- [ ] **Step 4: Update `_control_ssh`**

Replace the opening of `_control_ssh` so it resolves through the cache:

```python
def _control_ssh(command, timeout=20):
    """Module-level SSH exec for the control API (no per-sync stats).
    Returns (stdout, returncode)."""
    try:
        host, _ = vicon_host.resolve_vicon_host_cached(probe_port=22)
    except vicon_host.ViconOffline as exc:
        logger.warning(f"control SSH skipped, Vicon PC unreachable: {exc}")
        return "", 1
    escaped_command = command.replace('"', '\\"')
    full_cmd = (
        f'sshpass -p {SSH_PASS} ssh -o StrictHostKeyChecking=no '
        f'{SSH_USER}@{host} "{escaped_command}"'
    )
```

Leave the rest of the function (the `try`/`subprocess.run`/`except` block) unchanged.

- [ ] **Step 5: Add the `vicon` block to `/status`**

In `ControlHandler.do_GET`, replace the `/status` branch with:

```python
        if path == "/status":
            try:
                ip, node_name = vicon_host.resolve_vicon_host_cached(probe_port=22)
                vicon = {"host": ip, "dns_name": node_name, "online": True}
            except vicon_host.ViconOffline as exc:
                vicon = {"host": None, "dns_name": None, "online": False,
                         "error": str(exc)}
            self._send_json(200, {
                "ok": True,
                "vicon": vicon,
                "mocap": {
                    "running": SYNC_LOCK.locked(),
                    "state": LAST_RUN.get("state"),
                    "last_run": LAST_RUN.get("last_run"),
                    "started_at": LAST_RUN.get("started_at"),
                    "last_stats": LAST_RUN.get("last_stats"),
                },
                "blackmagic": get_blackmagic_status(),
            })
```

- [ ] **Step 6: Resolve at the top of each sync cycle**

In `main()`, inside `while True:` and inside the `try:`, replace:

```python
            # Clear any pending trigger now that we're starting a fresh cycle.
            TRIGGER_EVENT.clear()

            # Run sync (lock prevents concurrent runs from rogue invocations)
            with SYNC_LOCK:
```

with:

```python
            # Clear any pending trigger now that we're starting a fresh cycle.
            TRIGGER_EVENT.clear()

            # Locate the Vicon PC before touching SSH. It rejoins the tailnet
            # under a new node identity (and a new IP) after a reinstall, and
            # an unreachable PC must be an error rather than an empty scan.
            try:
                host, node_name = vicon_host.resolve_vicon_host(probe_port=22)
            except vicon_host.ViconOffline as exc:
                logger.error(f"Vicon PC unreachable: {exc}")
                LAST_RUN["state"] = "offline"
                if not dry_run:
                    monitor.send_heartbeat_with_stats(
                        status="error",
                        message=f"Vicon PC unreachable: {exc}",
                        stats={"error_type": "ViconOffline"},
                    )
                if run_once:
                    sys.exit(1)
                logger.info(
                    f"Retrying in {CLIENT_MONITOR_INTERVAL} seconds (or until /trigger)..."
                )
                if TRIGGER_EVENT.wait(timeout=CLIENT_MONITOR_INTERVAL):
                    logger.info("Manual trigger received — retrying now")
                continue

            logger.info(f"Vicon PC found: {node_name} at {host}")

            # Run sync (lock prevents concurrent runs from rogue invocations)
            with SYNC_LOCK:
```

Then change the constructor call inside the lock from `ViconSync(dry_run=dry_run, cache=cache)` to:

```python
                syncer = ViconSync(host=host, dry_run=dry_run, cache=cache)
```

- [ ] **Step 7: Update the `--help` text**

In `main()`, replace the host line in the help output:

```python
        print(f"  Host:     {SSH_USER}@<discovered vicon* tailnet peer>")
```

- [ ] **Step 8: Update the Task 5 test helper**

In `tests/test_sync_vicon_rsync.py`, `make_syncer` must now pass a host:

```python
def make_syncer(tmp_path):
    cache = svr.SyncCache(str(tmp_path / "cache.json"))
    return svr.ViconSync(host="100.0.0.1", dry_run=True, cache=cache)
```

- [ ] **Step 9: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, 53 passed

- [ ] **Step 10: Verify the help text runs without a tailnet call**

Run: `python3 sync_vicon_rsync.py --help`
Expected: exits 0, prints `Host:     vicon@<discovered vicon* tailnet peer>`, and does not hang

- [ ] **Step 11: Commit**

```bash
git add sync_vicon_rsync.py tests/test_sync_vicon_rsync.py
git commit -m "Resolve Vicon host from tailnet in sync script

Removes the hardcoded 100.83.229.92, which belonged to a node that went
offline on 2026-06-29. An unreachable PC now skips the cycle with an
error heartbeat instead of two 60s SSH timeouts reported as success."
```

---

### Task 7: Wire discovery into the FTP monitor

**Files:**
- Modify: `ftp_monitor.py` — imports and `FtpConnectionManager.connect()` (`:186-212`)
- Modify: `monitor_config.json:3`
- Test: `tests/test_ftp_monitor.py` (new file)

**Interfaces:**
- Consumes: `vicon_host.resolve_vicon_host`, `vicon_host.DEFAULT_PREFIX`, `vicon_host.ViconOffline`.
- Produces: nothing consumed by later tasks.

`ViconOffline` is an `Exception` subclass, so the existing `except Exception` in the retry loop catches it and applies the existing backoff unchanged. The port probed is 21, the port this daemon actually uses.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ftp_monitor.py`:

```python
import pytest

import ftp_monitor
import vicon_host


def make_config():
    return {
        "ftp": {"user": "vicon", "password": "pw", "timeout": 1,
                "host_prefix": "vicon", "base_path": "/e/Recordings"},
        "monitoring": {"max_connection_retries": 1, "retry_backoff_base": 1,
                       "retry_backoff_max": 1},
    }


class FakeFTP:
    connected_to = None

    def __init__(self, timeout=None):
        pass

    def connect(self, host):
        FakeFTP.connected_to = host

    def login(self, user, password):
        pass


def test_connect_uses_resolved_host(monkeypatch):
    FakeFTP.connected_to = None
    monkeypatch.setattr(ftp_monitor, "FTP", FakeFTP)
    monkeypatch.setattr(
        ftp_monitor.vicon_host, "resolve_vicon_host",
        lambda **kw: ("100.111.64.24", "vicon-sb001869-1"),
    )
    mgr = ftp_monitor.FtpConnectionManager(make_config())
    assert mgr.connect() is True
    assert FakeFTP.connected_to == "100.111.64.24"


def test_connect_probes_port_21(monkeypatch):
    seen = {}
    monkeypatch.setattr(ftp_monitor, "FTP", FakeFTP)
    monkeypatch.setattr(
        ftp_monitor.vicon_host, "resolve_vicon_host",
        lambda **kw: seen.update(kw) or ("100.111.64.24", "n"),
    )
    ftp_monitor.FtpConnectionManager(make_config()).connect()
    assert seen["probe_port"] == 21


def test_connect_returns_false_when_vicon_offline(monkeypatch):
    def boom(**kw):
        raise vicon_host.ViconOffline("no vicon* peer online")

    monkeypatch.setattr(ftp_monitor.vicon_host, "resolve_vicon_host", boom)
    assert ftp_monitor.FtpConnectionManager(make_config()).connect() is False


def test_config_has_no_hardcoded_ip():
    source = open("monitor_config.json").read()
    assert "100.83.229.92" not in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ftp_monitor.py -q`
Expected: FAIL — `AttributeError: module 'ftp_monitor' has no attribute 'vicon_host'`

- [ ] **Step 3: Import the resolver**

In `ftp_monitor.py`, add to the imports block:

```python
import vicon_host
```

- [ ] **Step 4: Resolve inside `connect()`**

In `FtpConnectionManager.connect`, replace the body of the `try:` inside the retry loop. Change:

```python
            try:
                self.logger.info(f"Connecting to FTP server {self.config['ftp']['host']}...")
                self.ftp = FTP(timeout=self.config['ftp']['timeout'])
                self.ftp.connect(self.config['ftp']['host'])
                self.ftp.login(self.config['ftp']['user'], self.config['ftp']['password'])
```

to:

```python
            try:
                # The Vicon PC changes tailnet address when it rejoins, so
                # resolve on every attempt rather than trusting a fixed host.
                host, node_name = vicon_host.resolve_vicon_host(
                    prefix=self.config['ftp'].get('host_prefix', vicon_host.DEFAULT_PREFIX),
                    probe_port=21,
                    timeout=self.config['ftp']['timeout'],
                )
                self.logger.info(f"Connecting to FTP server {node_name} at {host}...")
                self.ftp = FTP(timeout=self.config['ftp']['timeout'])
                self.ftp.connect(host)
                self.ftp.login(self.config['ftp']['user'], self.config['ftp']['password'])
```

Leave the `except Exception` handler and backoff below it unchanged — it already logs the failure and retries, and `ViconOffline` is an `Exception`.

- [ ] **Step 5: Update the config**

In `monitor_config.json`, in the `"ftp"` block, replace:

```json
    "host": "100.83.229.92",
```

with:

```json
    "host_prefix": "vicon",
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, 57 passed

- [ ] **Step 7: Confirm no hardcoded address survives anywhere**

Run: `grep -rn "100\.83\.229\.92" --include="*.py" --include="*.json" . | grep -v sync_cache.json | grep -v monitor_state.json | grep -v monitor_output.json`
Expected: no output (the three large state/cache JSON files are historical data, not configuration)

- [ ] **Step 8: Commit**

```bash
git add ftp_monitor.py monitor_config.json tests/test_ftp_monitor.py
git commit -m "Resolve Vicon host from tailnet in FTP monitor

Replaces the hardcoded ftp.host with host_prefix discovery, probing
port 21 so the check matches the port this daemon uses."
```

---

### Task 8: Live verification against the tailnet

**Files:** none modified — this task only runs things and records the result.

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: confirmation the spec's Verification section holds.

The Vicon PC was reachable at `100.111.64.24` on ports 22 and 21 as of 2026-08-05. If it has gone offline again by the time this task runs, the expected results invert: every command should report the offline condition cleanly, which is itself the behaviour under test. Record which case you observed.

- [ ] **Step 1: Resolve the host directly**

Run: `python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=22))"`
Expected: `('100.111.64.24', 'vicon-sb001869-1')` — or a `ViconOffline` naming each known peer and its `LastSeen`

- [ ] **Step 2: Confirm the FTP port resolves too**

Run: `python3 -c "import vicon_host; print(vicon_host.resolve_vicon_host(probe_port=21))"`
Expected: the same tuple, proving both daemons' ports are open on the discovered node

- [ ] **Step 3: Run a dry-run sync end to end**

Run: `python3 sync_vicon_rsync.py --once --dry-run 2>&1 | tail -30`
Expected: a `Vicon PC found: vicon-sb001869-1 at 100.111.64.24` line, a non-zero date-directory count for at least one source, and `Errors: 0`. If the PC is offline, expect `Vicon PC unreachable:` and exit 1 with no SSH timeout delay.

- [ ] **Step 4: Confirm the error path is honest**

Run: `python3 -c "
import tempfile, os
import sync_vicon_rsync as svr
cache = svr.SyncCache(os.path.join(tempfile.mkdtemp(), 'verify_cache.json'))
s = svr.ViconSync(host='100.0.0.1', dry_run=True, cache=cache)
s.ssh_execute = lambda cmd, timeout=60: ('', 1)
print('listing ->', s.list_date_directories('E:\\\\Recordings'))
"`
Expected: `listing -> None`, confirming a failed listing is no longer indistinguishable from an empty directory

- [ ] **Step 5: Restart the services and check the control API**

Run:
```bash
sudo systemctl restart vicon-sync-rsync.service vicon-ftp-monitor.service
sleep 5
curl -s localhost:8765/status | python3 -m json.tool
```
Expected: a `"vicon"` block with `"online": true`, `"host": "100.111.64.24"`, `"dns_name": "vicon-sb001869-1"`

- [ ] **Step 6: Confirm the FTP monitor connects**

Run: `tail -20 logs/ftp_monitor.log`
Expected: `Connecting to FTP server vicon-sb001869-1 at 100.111.64.24...` followed by `FTP connected successfully` — replacing the `Connection failed (attempt N/5): timed out` loop

- [ ] **Step 7: Commit any log or doc corrections**

If Steps 1-6 revealed a discrepancy with the spec, fix it and commit:

```bash
git add -A
git commit -m "Fix issues found during live verification"
```

If everything passed, there is nothing to commit — say so explicitly rather than creating an empty commit.

---

## Notes for the reviewer

- Tasks 1-4 are pure additions with no effect on running services; they can be reviewed and merged independently of Tasks 5-7.
- Task 5 is independent of discovery entirely. If Tasks 6-7 stall for any reason, Task 5 alone already converts the silent failure into a visible one.
- Task 8 requires the Vicon PC to be online for its positive assertions. Its negative assertions (clean offline reporting) are testable either way.

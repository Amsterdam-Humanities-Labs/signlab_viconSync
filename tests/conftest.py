import pytest

import vicon_credentials


@pytest.fixture(autouse=True)
def stub_vicon_password(monkeypatch):
    """Keep the whole suite off the real credential.

    Tests that build sshpass/FTP commands need *a* password, but none of them
    need the real one, and a developer machine with a filled-in
    monitor_config.json must not quietly feed it into a test run. Setting the
    environment variable also means the suite passes on a fresh clone, which
    has no monitor_config.json at all.

    Tests that exercise the lookup itself delenv this and supply their own
    config; the cache is cleared on both sides so neither leaks into the other.
    """
    monkeypatch.setenv("VICON_PASSWORD", "test-password")
    vicon_credentials.get_vicon_password.cache_clear()
    yield
    vicon_credentials.get_vicon_password.cache_clear()

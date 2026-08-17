import json

import pytest

import vicon_credentials


@pytest.fixture(autouse=True)
def clear_cache():
    """get_vicon_password caches; every test must start from a cold lookup."""
    vicon_credentials.get_vicon_password.cache_clear()
    yield
    vicon_credentials.get_vicon_password.cache_clear()


def write_config(tmp_path, password):
    path = tmp_path / "monitor_config.json"
    path.write_text(json.dumps({"ftp": {"user": "vicon", "password": password}}))
    return path


def test_reads_password_from_config(tmp_path, monkeypatch):
    monkeypatch.delenv("VICON_PASSWORD", raising=False)
    config = write_config(tmp_path, "s3cret")
    assert vicon_credentials.get_vicon_password(config) == "s3cret"


def test_environment_wins_over_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VICON_PASSWORD", "from-env")
    config = write_config(tmp_path, "from-file")
    assert vicon_credentials.get_vicon_password(config) == "from-env"


def test_missing_config_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.delenv("VICON_PASSWORD", raising=False)
    missing = tmp_path / "monitor_config.json"
    with pytest.raises(vicon_credentials.MissingCredentials, match="does not exist"):
        vicon_credentials.get_vicon_password(missing)


def test_placeholder_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("VICON_PASSWORD", raising=False)
    config = write_config(tmp_path, vicon_credentials.PLACEHOLDER)
    with pytest.raises(vicon_credentials.MissingCredentials, match="placeholder"):
        vicon_credentials.get_vicon_password(config)


def test_config_without_ftp_password_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("VICON_PASSWORD", raising=False)
    path = tmp_path / "monitor_config.json"
    path.write_text(json.dumps({"ftp": {"user": "vicon"}}))
    with pytest.raises(vicon_credentials.MissingCredentials, match="no ftp.password"):
        vicon_credentials.get_vicon_password(path)


def test_unparseable_config_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("VICON_PASSWORD", raising=False)
    path = tmp_path / "monitor_config.json"
    path.write_text("{not json")
    with pytest.raises(vicon_credentials.MissingCredentials, match="not valid JSON"):
        vicon_credentials.get_vicon_password(path)


def test_no_tracked_file_carries_a_literal_password():
    """The scripts must ask vicon_credentials, never inline a credential."""
    for name in ("sync_vicon_rsync.py", "cleanup_vicon.py", "resync_fbx.py"):
        source = open(name).read()
        assert "get_vicon_password" in source, name
        assert "SSH_PASS =" not in source, name
        assert "FTP_PASS =" not in source, name

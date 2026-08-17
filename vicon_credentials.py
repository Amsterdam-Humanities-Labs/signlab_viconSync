"""Resolve the Vicon account password without keeping it in the repo.

The password used to sit in three scripts as a literal, which meant every
clone of this repo shipped a working credential. It now lives only in the
untracked monitor_config.json (or in VICON_PASSWORD for callers that would
rather not have a config file at all), and the scripts ask for it here.

Lookup is lazy and cached: importing a script must not require the config to
exist, but a sync that scp's thousands of files must not re-read it per file.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("monitor_config.json")
PLACEHOLDER = "CHANGE_ME"


class MissingCredentials(Exception):
    """No usable Vicon password is configured on this machine."""


@lru_cache(maxsize=None)
def get_vicon_password(config_file=CONFIG_FILE):
    """Return the Vicon account password, or raise MissingCredentials.

    VICON_PASSWORD wins when set; otherwise the value comes from
    monitor_config.json -> ftp.password. The same password serves both the FTP
    login and the sshpass calls, which is why one lookup covers all callers.
    """
    from_env = os.environ.get("VICON_PASSWORD")
    if from_env:
        return from_env

    path = Path(config_file)
    if not path.exists():
        raise MissingCredentials(
            f"no VICON_PASSWORD in the environment and {path} does not exist "
            f"(copy monitor_config.example.json to monitor_config.json and set "
            f"ftp.password)"
        )
    try:
        password = json.loads(path.read_text())["ftp"]["password"]
    except ValueError as exc:
        raise MissingCredentials(f"{path} is not valid JSON: {exc}")
    except KeyError:
        raise MissingCredentials(f"{path} has no ftp.password entry")

    if not password or password == PLACEHOLDER:
        raise MissingCredentials(
            f"ftp.password in {path} is still the {PLACEHOLDER} placeholder"
        )
    return password

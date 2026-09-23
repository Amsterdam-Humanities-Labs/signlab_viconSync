"""Old name, kept so existing callers keep working (signlab_signcollect-stack#51).

pythonCron and the vicon-sync-rsync systemd unit on the core server start this
path. The code lives in sync_vicon_files.py.
"""
from sync_vicon_files import *  # noqa: F401,F403  old name, #51

if __name__ == "__main__":
    from sync_vicon_files import main
    main()

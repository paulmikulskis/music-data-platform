"""Keep representative statistics text in bounded volatile shared memory, never PGDATA."""

import os
import pwd
import shutil
from pathlib import Path

root = Path(os.environ["PGDATA"])
if (root / "PG_VERSION").exists():
    account = pwd.getpwnam("postgres")
    target = Path("/dev/shm/mdp-query-statistics")
    target.mkdir(mode=0o700, exist_ok=True)
    os.chown(target, account.pw_uid, account.pw_gid)
    path = root / "pg_stat_tmp"
    if not path.is_symlink():
        if path.exists():
            shutil.rmtree(path)
        path.symlink_to(target, target_is_directory=True)
    # Retire files from the former statement logger before accepting connections.
    old = root / "query-log"
    if old.exists() and not old.is_symlink():
        shutil.rmtree(old)

    (root / "pg_stat/pg_stat_statements.stat").unlink(missing_ok=True)

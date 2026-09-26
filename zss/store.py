"""History file access: locking, load/delete/backup/restore, block list.

All mutation goes through `history_lock()`, which takes zsh's own
`$HISTFILE.LOCK` (a hard link of a temp file, retried for ~10s, considered
stale after 10s — the same rule zsh itself uses) plus an `fcntl` lock on the
history file, matching what zsh does when `HIST_FCNTL_LOCK` is set.
"""

import fcntl
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from zss import history

LOCK_TIMEOUT = 10.0
LOCK_STALE_AFTER = 10.0  # zsh's own rule for when a $HISTFILE.LOCK is abandoned; independent of the caller's wait timeout
MAX_BACKUPS = 20


@dataclass(frozen=True)
class Paths:
    histfile: Path
    data_dir: Path
    backups_dir: Path
    blocklist_file: Path
    colors_file: Path


def paths() -> Paths:
    histfile = os.environ.get("ZSS_HISTFILE") or os.environ.get("HISTFILE") or str(Path.home() / ".zsh_history")
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    data_dir = Path(data_home) / "zsh-smart-suggest"
    return Paths(
        histfile=Path(histfile),
        data_dir=data_dir,
        backups_dir=data_dir / "backups",
        blocklist_file=data_dir / "blocklist.txt",
        colors_file=data_dir / "colors.conf",
    )


class LockTimeout(Exception):
    pass


@contextmanager
def history_lock(p: Optional[Paths] = None, timeout: float = LOCK_TIMEOUT):
    p = p or paths()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    p.histfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile = Path(str(p.histfile) + ".LOCK")
    tmp = Path(str(p.histfile) + f".zss-lock-tmp-{os.getpid()}")
    tmp.write_text(str(os.getpid()))
    deadline = time.time() + timeout
    try:
        while True:
            try:
                os.link(tmp, lockfile)
                break
            except FileExistsError:
                try:
                    age = time.time() - lockfile.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > LOCK_STALE_AFTER:
                    try:
                        lockfile.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.time() > deadline:
                    raise LockTimeout(f"Timed out waiting for lock on {p.histfile}")
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

    fd = os.open(str(p.histfile), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX)
        try:
            yield p
        finally:
            fcntl.lockf(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        try:
            lockfile.unlink()
        except FileNotFoundError:
            pass


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _write_in_place(path: Path, data: bytes) -> None:
    """Overwrite `path`'s existing inode in place, instead of replacing it
    with a renamed temp file. A process that opened the histfile before this
    call (as zsh does: it keeps its own fd open across the shell session for
    incremental history appends) still writes into the SAME inode afterward.
    A rename-based write would leave that fd pointing at an orphaned inode,
    silently losing whatever that process appends next."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".zss-tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def load(p: Optional[Paths] = None) -> List[history.Entry]:
    p = p or paths()
    return history.parse(_read_bytes(p.histfile))


@dataclass(frozen=True)
class Command:
    text: str
    count: int
    last_index: int
    last_ts: Optional[int]


def unique_commands(entries: List[history.Entry]) -> List[Command]:
    stats: Dict[str, Command] = {}
    for idx, e in enumerate(entries):
        prev = stats.get(e.text)
        count = (prev.count if prev else 0) + 1
        stats[e.text] = Command(text=e.text, count=count, last_index=idx, last_ts=e.ts)
    return list(stats.values())


# ---- backups ----

def _backup_name(prefix_time: str, n: int) -> str:
    return f"zsh_history.{prefix_time}.{n}"


def create_backup(p: Paths) -> Optional[str]:
    """Snapshot the current history file. Returns the backup id, or None if
    there is no history file yet."""
    if not p.histfile.exists():
        return None
    p.backups_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    n = 0
    while True:
        name = _backup_name(ts, n)
        dest = p.backups_dir / name
        if not dest.exists():
            break
        n += 1
    data = p.histfile.read_bytes()
    _atomic_write(dest, data)
    prune_backups(p)
    return name


def prune_backups(p: Paths, keep: int = MAX_BACKUPS) -> None:
    if not p.backups_dir.exists():
        return
    entries = sorted(p.backups_dir.glob("zsh_history.*"), key=lambda f: f.stat().st_mtime)
    excess = len(entries) - keep
    for old in entries[:max(excess, 0)]:
        old.unlink(missing_ok=True)


def list_backups(p: Optional[Paths] = None) -> List[str]:
    p = p or paths()
    if not p.backups_dir.exists():
        return []
    return sorted(f.name for f in p.backups_dir.glob("zsh_history.*"))


def restore(backup_id: str, p: Optional[Paths] = None) -> str:
    """Restore a backup over the current history file, backing up the
    current content first. Returns the id of that pre-restore backup."""
    p = p or paths()
    src = p.backups_dir / backup_id
    data = src.read_bytes()
    with history_lock(p):
        pre_restore_id = create_backup(p)
        _write_in_place(p.histfile, data)
    return pre_restore_id


# ---- delete ----

@dataclass(frozen=True)
class DeleteResult:
    removed: int
    backup_id: Optional[str]
    removed_texts: List[str] = field(default_factory=list)


def _matcher(patterns: List[str], match: str):
    if match == "exact":
        pats = set(patterns)
        return lambda text: text in pats
    if match == "prefix":
        return lambda text: any(text.startswith(pat) for pat in patterns)
    if match == "regex":
        compiled = [re.compile(pat) for pat in patterns]
        return lambda text: any(c.search(text) for c in compiled)
    raise ValueError(f"unknown match mode: {match}")


def count_matching(patterns: List[str], match: str = "prefix", p: Optional[Paths] = None) -> int:
    """Count history entries matching `patterns`, without deleting anything.
    Used to preview how many entries a delete or a new block rule would
    remove before the caller asks for confirmation."""
    p = p or paths()
    is_match = _matcher(patterns, match)
    return sum(1 for e in load(p) if is_match(e.text))


def delete(patterns: List[str], match: str = "exact", p: Optional[Paths] = None) -> DeleteResult:
    p = p or paths()
    is_match = _matcher(patterns, match)
    with history_lock(p):
        entries = history.parse(_read_bytes(p.histfile))
        kept = []
        removed_texts: List[str] = []
        removed = 0
        for e in entries:
            if is_match(e.text):
                removed += 1
                if e.text not in removed_texts:
                    removed_texts.append(e.text)
            else:
                kept.append(e)
        if removed == 0:
            return DeleteResult(removed=0, backup_id=None, removed_texts=[])
        backup_id = create_backup(p)
        _write_in_place(p.histfile, history.serialize(kept))
    return DeleteResult(removed=removed, backup_id=backup_id, removed_texts=removed_texts)


# ---- block list ----
#
# A block rule is a plain prefix string: any history entry (or new command)
# whose text starts with it is never suggested and never saved to history
# (see the plugin's `_zss_is_blocked` / `_zss_zshaddhistory`), regardless of
# what follows the prefix. Rules are added manually only (CLI `zss block
# add`, or the "Block" action in the TUI/web UI) — deleting history entries
# (`delete()` above) never adds one.


def _block_lock(p: Paths):
    p.data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p.blocklist_file), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.lockf(fd, fcntl.LOCK_EX)
    return fd


def _block_unlock(fd: int) -> None:
    fcntl.lockf(fd, fcntl.LOCK_UN)
    os.close(fd)


def block_list(p: Optional[Paths] = None) -> List[str]:
    p = p or paths()
    data = _read_bytes(p.blocklist_file)
    return [history.decode_block_rule(rec) for rec in history.split_logical_records(data) if rec]


def validate_block_rule(text: str) -> None:
    """Raise ValueError if `text` can't be stored as a block rule. A trailing
    backslash is rejected because the block-list file uses backslash-newline
    as its line continuation (same as the history file): `echo \\` would be
    read back merged with the NEXT rule, by both `block_list()` and the
    plugin's loader."""
    if not text:
        raise ValueError("block rule must not be empty")
    if text.endswith("\\"):
        raise ValueError("block rule must not end with a backslash")


@dataclass(frozen=True)
class BlockAddResult:
    added: bool  # False if this exact rule text was already blocked
    delete_result: DeleteResult  # existing history entries purged by this rule


def block_add(text: str, p: Optional[Paths] = None, position: Optional[int] = None) -> BlockAddResult:
    """Add a block rule (a no-op if it's already present) and purge every
    existing history entry that starts with it, same backup+lock path as
    `delete()`. Purging runs even when the rule already existed, so
    re-blocking (e.g. clicking "Block" again on a row) still cleans up any
    matching entries added since the rule was first set.

    A new rule is appended, or inserted at index `position` (clamped to the
    list) so undoing an unblock can put it back where it was. An existing
    rule is never moved."""
    validate_block_rule(text)
    if position is not None and position < 0:
        raise ValueError("position must be >= 0")
    p = p or paths()
    encoded = history.encode_block_rule(text)
    fd = _block_lock(p)
    try:
        data = _read_bytes(p.blocklist_file)
        records = history.split_logical_records(data)
        added = encoded not in records
        if added:
            if position is None:
                records.append(encoded)
            else:
                records.insert(min(position, len(records)), encoded)
            _atomic_write(p.blocklist_file, b"\n".join(records) + b"\n" if records else b"")
    finally:
        _block_unlock(fd)
    delete_result = delete([text], match="prefix", p=p)
    return BlockAddResult(added=added, delete_result=delete_result)


def block_remove(text: str, p: Optional[Paths] = None) -> bool:
    """Remove a block rule. Existing history entries are untouched either
    way — blocking/unblocking never deletes or restores history."""
    p = p or paths()
    encoded = history.encode_block_rule(text)
    fd = _block_lock(p)
    try:
        data = _read_bytes(p.blocklist_file)
        records = history.split_logical_records(data)
        if encoded not in records:
            return False
        records = [r for r in records if r != encoded]
        _atomic_write(p.blocklist_file, b"\n".join(records) + b"\n" if records else b"")
        return True
    finally:
        _block_unlock(fd)


@dataclass(frozen=True)
class BlockEditResult:
    found: bool  # False if `old` was not in the block list (nothing changed)
    delete_result: DeleteResult  # existing history entries purged by the new rule


def block_edit(old: str, new: str, p: Optional[Paths] = None) -> BlockEditResult:
    """Replace block rule `old` with `new` in place (same position in the
    list), then purge every history entry starting with `new`, same
    backup+lock path as `block_add()`. If `new` is already another rule, `old`
    is simply dropped so the list has no duplicates. Nothing is purged when
    `old` isn't found."""
    validate_block_rule(new)
    p = p or paths()
    old_enc = history.encode_block_rule(old)
    new_enc = history.encode_block_rule(new)
    fd = _block_lock(p)
    try:
        data = _read_bytes(p.blocklist_file)
        records = history.split_logical_records(data)
        if old_enc not in records:
            return BlockEditResult(found=False, delete_result=DeleteResult(removed=0, backup_id=None))
        if new_enc != old_enc:
            updated: List[bytes] = []
            for r in records:
                r = new_enc if r == old_enc else r
                if r not in updated:
                    updated.append(r)
            records = updated
            _atomic_write(p.blocklist_file, b"\n".join(records) + b"\n" if records else b"")
    finally:
        _block_unlock(fd)
    delete_result = delete([new], match="prefix", p=p)
    return BlockEditResult(found=True, delete_result=delete_result)

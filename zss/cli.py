import argparse
import sys
import time

from zss import __version__, colors, history, store

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2
EXIT_LOCK_TIMEOUT = 3


def _fmt_ts(ts):
    if not ts:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return "-"


def cmd_list(args) -> int:
    p = store.paths()
    cmds = store.unique_commands(store.load(p))
    if args.grep:
        cmds = [c for c in cmds if args.grep in c.text]
    if args.sort == "count":
        cmds.sort(key=lambda c: (-c.count, -c.last_index))
    else:
        cmds.sort(key=lambda c: -c.last_index)
    if args.limit is not None:
        cmds = cmds[: args.limit]

    if args.json:
        import json

        print(json.dumps([
            {"text": c.text, "count": c.count, "last_index": c.last_index, "last_ts": c.last_ts}
            for c in cmds
        ]))
        return EXIT_OK

    for c in cmds:
        first_line = c.text.split("\n", 1)[0]
        marker = " ⏎" if "\n" in c.text else ""
        print(f"{c.count:>4}  {_fmt_ts(c.last_ts):>16}  {first_line}{marker}")
    return EXIT_OK


def cmd_rm(args) -> int:
    p = store.paths()
    match = "prefix" if args.prefix else "regex" if args.regex else "exact"
    entries = store.load(p)
    from zss.store import _matcher

    is_match = _matcher(args.pattern, match)
    matched_texts = sorted({e.text for e in entries if is_match(e.text)})
    count = sum(1 for e in entries if is_match(e.text))

    if count == 0:
        print("no matching history entries", file=sys.stderr)
        return EXIT_NO_MATCH

    plural = "y" if count == 1 else "ies"
    print(f"{count} entr{plural} match ({len(matched_texts)} distinct command(s)):")
    for t in matched_texts[:20]:
        print(f"  {t!r}")
    if len(matched_texts) > 20:
        print(f"  ... and {len(matched_texts) - 20} more")

    if args.dry_run:
        return EXIT_OK

    if not args.yes:
        try:
            resp = input(f"Delete {count} entr{plural}? A backup is made first. [y/N] ")
        except EOFError:
            resp = ""
        if resp.strip().lower() != "y":
            print("aborted", file=sys.stderr)
            return EXIT_NO_MATCH

    result = store.delete(args.pattern, match=match, p=p)
    print(f"removed {result.removed} entr{plural}; backup {result.backup_id}")
    return EXIT_OK


def cmd_block(args) -> int:
    p = store.paths()
    if args.block_action == "list":
        for t in store.block_list(p):
            print(t)
        return EXIT_OK
    if args.block_action in ("add", "edit"):
        text = args.new if args.block_action == "edit" else args.text
        try:
            store.validate_block_rule(text)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return EXIT_USAGE
        if args.block_action == "edit" and args.old not in store.block_list(p):
            print("that rule is not in the block list", file=sys.stderr)
            return EXIT_NO_MATCH
        count = store.count_matching([text], match="prefix", p=p)
        if count:
            print(f"{count} history {'entry starts' if count == 1 else 'entries start'} with this and will be deleted.")
        if not args.yes:
            question = f"Change block rule \"{args.old}\" to \"{text}\"?" if args.block_action == "edit" else f"Block \"{text}\"?"
            try:
                resp = input(f"{question} [y/N] ")
            except EOFError:
                resp = ""
            if resp.strip().lower() != "y":
                print("aborted", file=sys.stderr)
                return EXIT_NO_MATCH
        try:
            if args.block_action == "edit":
                result = store.block_edit(args.old, text, p)
                if not result.found:
                    print("that rule is not in the block list", file=sys.stderr)
                    return EXIT_NO_MATCH
                msg = f"changed block rule {args.old!r} to {text!r}"
            else:
                result = store.block_add(text, p)
                msg = f"blocked: {text!r}" if result.added else f"already blocked: {text!r}"
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return EXIT_USAGE
        if result.delete_result.removed:
            plural = "y" if result.delete_result.removed == 1 else "ies"
            msg += f"; removed {result.delete_result.removed} entr{plural}; backup {result.delete_result.backup_id}"
        print(msg)
        return EXIT_OK
    if args.block_action == "rm":
        if store.block_remove(args.text, p):
            print("removed block rule; matching commands can be suggested and saved again")
            return EXIT_OK
        print("that rule is not in the block list", file=sys.stderr)
        return EXIT_NO_MATCH
    return EXIT_USAGE


def cmd_backup(args) -> int:
    p = store.paths()
    if args.backup_action == "list":
        for b in store.list_backups(p):
            print(b)
        return EXIT_OK
    if args.backup_action == "restore":
        backup_path = p.backups_dir / args.id
        if not backup_path.exists():
            print(f"no such backup: {args.id}", file=sys.stderr)
            return EXIT_NO_MATCH
        pre_restore_id = store.restore(args.id, p)
        print(f"restored {args.id}" + (f"; previous content backed up as {pre_restore_id}" if pre_restore_id else ""))
        return EXIT_OK
    if args.backup_action == "prune":
        before = len(store.list_backups(p))
        store.prune_backups(p)
        after = len(store.list_backups(p))
        print(f"pruned {before - after} old backup(s), {after} remaining")
        return EXIT_OK
    return EXIT_USAGE


def cmd_colors(args) -> int:
    p = store.paths()
    if args.colors_action == "list":
        current = colors.load(p)
        for key in colors.DEFAULTS:
            marker = "" if current[key] == colors.DEFAULTS[key] else "  (custom)"
            print(f"{key}={current[key]}{marker}")
        return EXIT_OK
    if args.colors_action == "set":
        try:
            colors.save({args.key: args.style}, p)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return EXIT_USAGE
        print(f"{args.key}={args.style}")
        return EXIT_OK
    if args.colors_action == "reset":
        try:
            colors.reset(args.key, p)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return EXIT_USAGE
        print(f"reset {args.key or 'all colours'} to default")
        return EXIT_OK
    return EXIT_USAGE


def cmd_doctor(args) -> int:
    p = store.paths()
    problems = 0

    print(f"history file:   {p.histfile}")
    if p.histfile.exists():
        print("  ok - exists")
    else:
        print("  warn - does not exist yet")

    lock_path = p.histfile.parent / (p.histfile.name + ".LOCK")
    if lock_path.exists():
        print(f"  warn - lock file present ({lock_path}); a shell may have exited uncleanly")

    print(f"data dir:       {p.data_dir}")
    try:
        p.data_dir.mkdir(parents=True, exist_ok=True)
        test_file = p.data_dir / ".zss-doctor-write-test"
        test_file.write_text("ok")
        test_file.unlink()
        print("  ok - writable")
    except OSError as e:
        print(f"  FAIL - not writable: {e}")
        problems += 1

    try:
        styles = colors.load(p)
        bad = [k for k, v in styles.items() if not colors.validate(v)]
        if bad:
            print(f"  FAIL - invalid colors.conf entries: {bad}")
            problems += 1
        else:
            print("colors.conf:    ok")
    except OSError as e:
        print(f"colors.conf:    FAIL - {e}")
        problems += 1

    zshrc = __import__("pathlib").Path.home() / ".zshrc"
    if zshrc.exists():
        text = zshrc.read_text(errors="replace")
        for marker, name in [
            ("zsh-autosuggestions", "zsh-autosuggestions"),
            ("fast-syntax-highlighting", "fast-syntax-highlighting"),
        ]:
            for line in text.splitlines():
                stripped = line.strip()
                if marker in stripped and stripped.startswith("source") and "zss:disabled" not in stripped:
                    print(f"  warn - ~/.zshrc still sources {name} (run install.sh to disable it)")
                    problems += 1
                    break

    print(f"\n{problems} problem(s) found" if problems else "\nno problems found")
    return EXIT_OK if problems == 0 else EXIT_NO_MATCH


def _repo_root():
    import os
    from pathlib import Path

    # abspath, not resolve(): keep symlinks so the path matches the one
    # install.sh wrote into ~/.zshrc and re-running it is a no-op.
    return Path(os.path.abspath(__file__)).parent.parent


def cmd_update(args) -> int:
    import subprocess

    root = _repo_root()
    if not (root / ".git").exists():
        print(f"{root} is not a git clone; reinstall with the curl one-liner from the README", file=sys.stderr)
        return EXIT_USAGE
    try:
        result = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"])
    except FileNotFoundError:
        print("git is not installed", file=sys.stderr)
        return EXIT_USAGE
    if result.returncode != 0:
        print(f"git pull failed in {root}", file=sys.stderr)
        return EXIT_NO_MATCH
    # Re-run the freshly pulled installer so changes to the ~/.zshrc block
    # reach existing installs; it shows a diff and asks before writing.
    installer = root / "install.sh"
    result = subprocess.run(["bash", str(installer)])
    if result.returncode != 0:
        print(f"Updated {root}, but ~/.zshrc was not changed; run {installer} to retry", file=sys.stderr)
        return EXIT_NO_MATCH
    print("Updated. Restart your shell (or run `exec zsh`) to pick up the change.")
    return EXIT_OK


def cmd_tui(args) -> int:
    from zss.tui import main as tui_main

    return tui_main()


def cmd_web(args) -> int:
    from zss.web import main as web_main

    return web_main(port=args.port, no_open=args.no_open, idle_exit=args.idle_exit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zss", description="zsh-smart-suggest history manager")
    parser.add_argument("--version", action="version", version=f"zss {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="list unique history commands")
    p_list.add_argument("--sort", choices=["recent", "count"], default="recent")
    p_list.add_argument("--grep", default=None)
    p_list.add_argument("--limit", type=int, default=None)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_rm = sub.add_parser("rm", help="delete matching history entries")
    group = p_rm.add_mutually_exclusive_group()
    group.add_argument("--exact", action="store_true")
    group.add_argument("--prefix", action="store_true")
    group.add_argument("--regex", action="store_true")
    p_rm.add_argument("--dry-run", action="store_true")
    p_rm.add_argument("-y", "--yes", action="store_true")
    p_rm.add_argument("pattern", nargs="+")
    p_rm.set_defaults(func=cmd_rm)

    p_block = sub.add_parser("block", help="manage blocked command prefixes")
    block_sub = p_block.add_subparsers(dest="block_action")
    block_add = block_sub.add_parser("add")
    block_add.add_argument("-y", "--yes", action="store_true")
    block_add.add_argument("text")
    block_sub.add_parser("list")
    block_rm = block_sub.add_parser("rm")
    block_rm.add_argument("text")
    block_edit = block_sub.add_parser("edit", help="change an existing rule; history matching the new rule is deleted")
    block_edit.add_argument("-y", "--yes", action="store_true")
    block_edit.add_argument("old")
    block_edit.add_argument("new")
    p_block.set_defaults(func=cmd_block)

    p_backup = sub.add_parser("backup", help="manage history backups")
    backup_sub = p_backup.add_subparsers(dest="backup_action")
    backup_sub.add_parser("list")
    backup_restore = backup_sub.add_parser("restore")
    backup_restore.add_argument("id")
    backup_sub.add_parser("prune")
    p_backup.set_defaults(func=cmd_backup)

    p_colors = sub.add_parser("colors", help="manage highlight colours")
    colors_sub = p_colors.add_subparsers(dest="colors_action")
    colors_sub.add_parser("list")
    colors_set = colors_sub.add_parser("set")
    colors_set.add_argument("key")
    colors_set.add_argument("style")
    colors_reset = colors_sub.add_parser("reset")
    colors_reset.add_argument("key", nargs="?", default=None)
    p_colors.set_defaults(func=cmd_colors)

    p_doctor = sub.add_parser("doctor", help="check the install for common problems")
    p_doctor.set_defaults(func=cmd_doctor)

    p_update = sub.add_parser("update", help="git pull the latest version, then re-run its installer")
    p_update.set_defaults(func=cmd_update)

    p_tui = sub.add_parser("tui", help="open the curses history manager")
    p_tui.set_defaults(func=cmd_tui)

    p_web = sub.add_parser("web", help="open the local web UI")
    p_web.add_argument("--port", type=int, default=7799)
    p_web.add_argument("--no-open", action="store_true")
    p_web.add_argument("--idle-exit", default=None, help="e.g. 30m; exit after this long with no requests")
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except store.LockTimeout as e:
        print(str(e), file=sys.stderr)
        return EXIT_LOCK_TIMEOUT

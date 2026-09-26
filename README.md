# zsh-smart-suggest

A from-scratch zsh plugin that replaces `zsh-autosuggestions` and
`fast-syntax-highlighting` with one dependency-free plugin, plus a
Python (stdlib-only) CLI/TUI/Web UI — `zss` — for managing what gets
suggested from your shell history.

- Inline grey history suggestions as you type, refreshed live across terminals.
- Built-in syntax highlighting (commands, strings, options, paths, ...).
- Hard-delete unwanted history entries (with an automatic backup first), from
  a key binding, a CLI, a curses TUI, or a local web UI.
- Block command prefixes manually (e.g. `printf `) so anything starting with
  them is never suggested or saved to history again.

## Install

One line, straight from GitHub (needs `git` and the macOS system `python3`):

```sh
curl -fsSL https://raw.githubusercontent.com/ThanhThi2895/zsh-smart-suggest/main/install.sh | bash
```

This clones the repo into `~/.zsh-smart-suggest` (or `git pull`s it if it's
already there) and then installs from that clone. It refuses to touch an
existing `~/.zsh-smart-suggest` that isn't a zsh-smart-suggest git clone. To clone somewhere
else, set `ZSS_INSTALL_DIR` for bash:

```sh
curl -fsSL https://raw.githubusercontent.com/ThanhThi2895/zsh-smart-suggest/main/install.sh | ZSS_INSTALL_DIR=~/src/zss bash
```

Already have a checkout? Run `./install.sh` from its root instead — it
installs that checkout in place and never clones anything.

Either way, the install step will:
1. Back up `~/.zshrc` to `~/.zshrc.zss-backup-<timestamp>` (mode `0600`).
2. Comment out the lines sourcing `zsh-autosuggestions` and
   `fast-syntax-highlighting` (matched by content, not line number — your
   `brew` packages are never touched or uninstalled).
3. Insert a marked block (`# >>> zsh-smart-suggest >>> ... # <<< ... <<<`)
   right where those lines were, setting `HISTSIZE`/`SAVEHIST` to 50000,
   `INC_APPEND_HISTORY`/`HIST_FCNTL_LOCK`, and sourcing this plugin.

It prints a diff of only the lines that would change and asks `[y/N]`
before writing anything (under `curl | bash` the question is read from your
terminal). It's safe to re-run (it replaces its own block instead of
duplicating it). It never prints, copies, or logs the rest of your
`~/.zshrc` (which may hold API tokens).

Restart your shell (or `exec zsh`) afterwards.

### Update

```sh
zss update
```

Runs `git pull --ff-only` in the directory `zss` was installed from (so
`~/.zsh-smart-suggest` for the one-line install), then re-runs that clone's
`install.sh` so any change to the `~/.zshrc` block reaches you too. The
installer shows its diff and asks `[y/N]` as usual, or says there's nothing
to do. Re-running the curl one-liner also pulls and re-installs; it also
switches the clone to `$ZSS_REPO_URL` / `$ZSS_BRANCH` if you set them.
Restart your shell afterwards.

### Uninstall

```sh
~/.zsh-smart-suggest/uninstall.sh    # or ./uninstall.sh from a checkout
```

Removes the block and uncomments the two lines it had disabled, so you're
back to `zsh-autosuggestions` + `fast-syntax-highlighting`. Also backs up
`~/.zshrc` first and asks for confirmation. If that succeeds and
`~/.zsh-smart-suggest` (or `$ZSS_INSTALL_DIR`) exists, it then asks
`[y/N]` whether to delete that clone too.

## Key bindings

| Key | Action |
|---|---|
| `→` / `End` | Accept the whole suggestion |
| `Alt-→` / `Alt-f` | Accept one word of the suggestion |
| `Ctrl-X Ctrl-D` | Delete the current suggestion from history (with backup); doesn't block it — see below |

Run `zss-toggle` in any shell to turn suggestions on/off for that session.

## `zss` commands

```
zss list [--sort recent|count] [--grep TEXT] [--limit N] [--json]
zss rm [--exact|--prefix|--regex] [--dry-run] [-y] PATTERN...
zss block add [-y] PREFIX
zss block list
zss block rm PREFIX
zss block edit [-y] OLD NEW
zss backup list
zss backup restore ID
zss backup prune
zss colors list
zss colors set KEY STYLE
zss colors reset [KEY]
zss doctor
zss update
zss tui
zss web [--port 7799] [--no-open] [--idle-exit 30m]
```

Exit codes: `0` ok, `1` nothing matched, `2` usage/invalid input, `3` lock timeout.

### How deletion works

`zss rm` (and the TUI, the web UI, and `Ctrl-X Ctrl-D`) all go through the
same path:

1. A backup of the whole history file is written to
   `${XDG_DATA_HOME:-~/.local/share}/zsh-smart-suggest/backups/` before any
   change (the last 20 are kept).
2. Every matching entry is removed from `~/.zsh_history` (byte-exact for
   everything else — multi-line entries and non-ASCII text round-trip
   correctly).

Deleting is otherwise a one-off cleanup: it does **not** stop the command
from being suggested or saved again — for that, block it explicitly (below).
Because zsh's own history reload (`fc -RI`) only ever adds entries it hasn't
seen yet, a shell that already had the deleted command loaded in memory
before you deleted it may still offer it as a suggestion until that shell is
restarted; `Ctrl-X Ctrl-D` avoids this for the shell you pressed it in by
also noting, in memory only for that shell, not to re-suggest that exact
command until you run it again.

### How blocking works

Blocking is manual only — nothing is ever blocked automatically, including
by deleting it. Add a rule with `zss block add PREFIX`, press `k` on a row in
the TUI, or use the edit (pencil) button on a row in the web UI's History
view. Both start from that row's full command text, which you can trim down —
e.g. to `printf ` — and show how many history entries will be deleted before
you confirm.

A block rule is a plain **prefix**: any command that starts with it, no
matter what follows, is never suggested and `zshaddhistory` refuses to save
it to history again. Adding a rule also deletes (with the same backup) every
existing history entry that already matches it — the CLI/TUI/web UI all show
how many that will be first. Blocking does not touch entries that don't
match. Remove a rule with `zss block rm PREFIX` (or "Allow again" in the
TUI/web UI); this never restores anything it previously deleted, it just lets
matching commands be suggested and saved again. Undoing an unblock in the
web UI puts the rule back at its old position (`POST /api/blocklist` takes an
optional `"position"` index). If history entries matching the rule were
added in the meantime, it shows how many would be deleted and asks first.

Change a rule in place — it keeps its position in the list — with `zss
block edit OLD NEW`, `e` in the TUI's Blocked view, or the pencil button on a
row in the web UI's Blocked view (`POST /api/blocklist/edit` with `{"old",
"new"}`). Like adding,
this deletes (with a backup) every history entry starting with the new
prefix. A rule cannot end with a backslash, because the block-list file uses
a trailing backslash as its line continuation.

### Restoring a backup

```sh
zss backup list
zss backup restore <id>
```

Restoring itself makes a backup of whatever was current first, so a restore
is reversible too.

## Web UI

`zss web` starts a **local-only** server (binds to `127.0.0.1`, never
started automatically by anything else — you always launch it yourself)
and prints a URL with a random per-run token in it, e.g.:

```
zss web UI: http://127.0.0.1:7799/?t=<64 hex chars>
```

Every API request must carry that token (`X-ZSS-Token` header or the `?t=`
query param for the initial page load); requests with the wrong `Host` or a
foreign `Origin` are rejected. Ctrl-C stops the server; `--idle-exit 30m`
exits automatically after that long with no requests.

Views (sidebar, or keys `1`-`4`):
- **History** — every distinct command, syntax-highlighted, with run count and
  last use; search (`/`), sort by recent/most-used, multi-select (`x` or the
  checkboxes) and delete the selection from the floating bar (confirms the
  count; a backup is made first). The pencil button (or `e`) opens an inline
  editor prefilled with the full command: trim it to a prefix, see how many
  history entries it matches, then "Block prefix" or "Delete matching only".
  The trash button deletes every run of that exact command.
- **Blocked** — the block-list prefixes, each with how many history entries
  currently match it; add one directly, edit one in place (pencil / `e`), or
  unblock it.
- **Backups** — every backup grouped by day, with "Restore".
- **Colors** — one row per highlight class, edited as a style string
  (`fg=cyan,bold`) with a live terminal preview; invalid values are flagged
  before saving. Saved changes apply in every open shell at its next prompt —
  nothing needs restarting.

Deletes, blocks, rule edits and restores show a toast with **Undo**, which
puts the rule back (when one changed) and restores the backup taken just
before.

### TUI keys

`zss tui` — History: `/` filter, `↑`/`↓` move, space mark, `d` delete, `k`
block, `s` sort, `t` Blocked view, `b` Backups view, `q` quit. Blocked view:
`e` edit rule in place, `a` allow again, `q` back. Backups view: `r` restore.

## Configuration

Set these before the plugin is sourced (or any time — most are read live):

| Variable | Default | Meaning |
|---|---|---|
| `ZSS_HIGHLIGHT_STYLE` | *(unset)* | Ghost-suggestion style; overrides the `suggestion` colour key when set |
| `ZSS_MAX_BUFFER` | `300` | Don't suggest above this many characters |
| `ZSS_IGNORE_PATTERN` | *(none)* | zsh glob; a matching buffer never gets a suggestion |
| `ZSS_DISABLE` | `0` | Disable suggestions entirely |
| `ZSS_HL_MAX_BUFFER` | `2000` | Don't highlight above this many characters |
| `ZSS_HL_DISABLE` | `0` | Disable only the highlighter (suggestions keep working) |

## Highlight classes and colours

Colours live in `${XDG_DATA_HOME:-~/.local/share}/zsh-smart-suggest/colors.conf`
(`zss colors set/reset`, or the Web UI's Colors tab — same file, same
validation). It's plain `key=style` data parsed line by line: never sourced,
never `eval`'d, so an invalid or malicious value is simply ignored, not
executed.

Style grammar: `none`, or a comma-separated list of `fg=COLOR`, `bg=COLOR`,
`bold`, `underline`, `standout`, where `COLOR` is a name (`black red green
yellow blue magenta cyan white default`), a number `0`-`255`, or `#rrggbb`.

| Key | What it colours | Default |
|---|---|---|
| `command` | external command / executable path | `fg=green` |
| `alias` | an alias | `fg=cyan` |
| `builtin` | a shell builtin | `fg=green,bold` |
| `function` | a shell function | `fg=blue` |
| `reserved` | a reserved word (`if`, `then`, ...) | `fg=yellow` |
| `unknown` | command-position word not found anywhere | `fg=red,bold` |
| `option` | `-x`, `--long`, `--long=value` | `fg=magenta` |
| `string` | quoted strings (incl. unterminated) | `fg=yellow` |
| `variable` | `$name`, `${...}` | `fg=cyan` |
| `path` | an argument that exists on disk | `underline` |
| `glob` | unquoted `*`, `?`, `[` | `fg=blue` |
| `comment` | `# ...` (with `setopt interactivecomments`) | `fg=8` |
| `operator` | `;` `&&` `\|\|` `\|` `&` `(` `)` `{` `}` | `fg=magenta,bold` |
| `redirect` | `>` `>>` `<` `2>` `&>` `<<` | `fg=magenta` |
| `assignment` | `NAME=value` in command position | `fg=blue` |
| `suggestion` | the ghost history suggestion | `fg=8` |

## Development

```sh
python3 -m unittest discover -s tests -v
zsh tests/zsh/run.zsh
```

See `docs/architecture.md` for how the pieces fit together and the history
file format notes (metafication, multi-line entries, locking).

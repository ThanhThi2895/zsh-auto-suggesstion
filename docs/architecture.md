# Architecture

```
 zsh session                              zss (Python 3.9 stdlib only, no deps)
 ┌──────────────────────────────┐          ┌─────────────────────────────────────────┐
 │ zsh-smart-suggest.plugin.zsh │          │ zss/history.py  parse/serialize/metafy   │
 │  - ZLE widgets, POSTDISPLAY  │  reads   │ zss/store.py    lock, backup, atomic     │
 │  - search $history (memory)  │◄──────── │                 rewrite, block list      │
 │  - skip blocked-prefix match │block list│ zss/cli.py      list/search/rm/block/…   │
 │  - zshaddhistory: block them │  file    │ zss/tui.py      curses manager           │
 │  - ^X^D: zss rm <suggestion> │────────► │ zss/web.py      http.server 127.0.0.1    │
 │ lib/highlight.zsh            │          │ zss/colors.py   colors.conf read/write   │
 │  - zle-line-pre-redraw hook  │  reads   │ zss/web/index.html  vanilla JS UI        │
 │  - region_highlight memo=    │◄──────── │   (History / Blocked / Backups / Colors) │
 │    zss-hl, styles from conf  │colors.conf└─────────────────────────────────────────┘
 └──────────────────────────────┘
            ~/.zsh_history  ◄──── lock ───► zss
 state: ${XDG_DATA_HOME:-~/.local/share}/zsh-smart-suggest/{blocklist.txt,colors.conf,backups/}
```

## Components

- **`zsh-smart-suggest.plugin.zsh`** — the suggester. Searches zsh's own
  in-memory `$history` (via `zmodload zsh/parameter`), newest first, using
  `${history[(r)pattern*]}` (a native, C-level first-match search — far
  faster at 50k+ entries than materializing `${(v)history}` and looping, or
  `:#` glob-filtering, which computes every match instead of just the
  first). A negative-match cache remembers the longest buffer prefix that
  had no match anywhere in history, so typing further into text that will
  never match doesn't re-scan on every keystroke.

  A `preexec` hook compares the history file's mtime against what was last
  seen and, on a change, runs `fc -RI` (merge new entries without
  duplicating what's already in memory); a `precmd` hook does the same for
  the block list's and `colors.conf`'s mtimes and reloads the block
  rules/colours. `fc -RI` must not run from `precmd`: there, zsh has already
  linked the pending command line into its history ring, and the reload
  leaves `HISTNO` pointing at no entry, so Up/Down do nothing at that
  prompt (`tests/zsh/test-history-nav.zsh`). The cost is that another
  terminal's new commands are picked up once this shell runs its next
  command, not on an empty Enter. This is what lets a command run in
  one terminal show up as a suggestion in another without restarting either
  — but `fc -RI` only ever *adds* entries it hasn't seen yet, so a *deleted*
  entry stays in an already-running shell's in-memory `$history` until that
  shell restarts (deleting doesn't add a block rule, see below); `^X^D`
  works around this for its own shell with an in-memory-only, non-persistent
  "don't re-suggest this exact command" note (`_zss_locally_deleted`), which
  is cleared as soon as the command is run again.

- **`lib/highlight.zsh`** — the highlighter, sourced by the plugin. Runs
  from `zle-line-pre-redraw` (no widget-wrapping needed), tokenises the
  buffer with `${(Z+c+)BUFFER}`, tracks "command position" (start of line,
  after `;`/`&&`/`||`/`|`/`&`/`(`/`{`/`!`, after `if`/`then`/... , and after
  precommands like `sudo`), and classifies each token using only zsh's own
  hash-table parameters (`$commands`, `$aliases`, `$builtins`, `$functions`,
  `$reswords`) — never `whence` in a subshell. It owns only
  `region_highlight` entries tagged `memo=zss-hl`; the suggester's own
  `memo=zss` entries (and anything a third party added) are left alone.

  Word offsets are found by scanning forward through the raw buffer from
  the previous token's end, skipping whitespace and backslash-newline
  continuations, then verifying the slice matches the token exactly before
  trusting it — on a mismatch it stops highlighting the rest of that line
  rather than risk painting the wrong region.

- **`zss/`** — the Python side, stdlib-only (matches the macOS system
  `/usr/bin/python3`, so nothing needs installing). `zss/history.py` and
  `zss/store.py` are the only code that touches `~/.zsh_history`; the zsh
  plugin never rewrites it itself, only reads it and calls `zss rm` (which
  goes through the same lock/backup path as the CLI, TUI, and web UI).
  Deleting and blocking are independent: `store.delete()` never touches the
  block list, and `store.block_add()` (manual only — CLI `zss block add`, or
  "Block" in the TUI/web UI) adds a prefix rule and then purges any existing
  entries that already match it through the same `delete()` path.

## History file format

- Two line formats coexist in real files: plain (`<cmd>`) and extended
  (`: <start-ts>:<elapsed>;<cmd>`), detected **per entry** since a file's
  format can change over time (e.g. after `setopt EXTENDED_HISTORY` was
  turned on partway through).
- **Multi-line entries**: a line ending in an *odd* number of backslashes
  right before its newline continues onto the next physical line (the last
  backslash is the escape for the newline; any earlier ones are literal).
- **Metafication**: zsh escapes NUL and bytes `0x83`-`0xA2` in the history
  file as `0x83` followed by the byte XOR `0x20` (`0x83` is zsh's own `Meta`
  marker, and `0x83`-`0xA2` is the range its internal token table reserves,
  so a real byte in that range must be escaped to avoid colliding with a
  token). Everything else, including ordinary multi-byte UTF-8 sequences,
  passes through unescaped. `zss/history.py`'s `unmetafy`/`metafy` implement
  exactly this rule; **unchanged entries are never re-derived from decoded
  text** — their original raw bytes are kept and re-emitted verbatim, which
  is what makes a delete round-trip byte-exact for every entry it didn't
  touch, metafication quirks and all.
- **Block list** (`blocklist.txt`) is a separate, much simpler format: plain
  UTF-8 text with real embedded newlines re-escaped as backslash-newline
  (the same continuation rule as the history file, so the same
  line-grouping logic parses both, and why a rule may not end with a
  backslash) — deliberately **not** metafied.
  Metafication is zsh's own internal-representation detail, applied
  transparently whenever zsh reads or writes *any* file; pre-metafying data
  ourselves before writing it would make zsh double-escape it on read. Each
  line is a **prefix** rule, not an exact match: any history entry or new
  command starting with it is blocked, whatever follows.

## Locking

`zss/store.py` takes zsh's own `$HISTFILE.LOCK` convention (a hard link of a
freshly-written temp file; a lock is considered abandoned after 10s, which
is independent of the caller's own wait timeout so a short timeout can't
accidentally steal a lock a slower operation still legitimately holds) plus
an `fcntl` lock on the history file itself (matching what zsh does when
`HIST_FCNTL_LOCK` is set). Entries are re-read *after* the lock is acquired,
so a shell that appends to the file concurrently (also via its own lock)
never loses that append when a deletion runs at the same time.

## Data paths

```
${XDG_DATA_HOME:-~/.local/share}/zsh-smart-suggest/
  blocklist.txt
  colors.conf
  backups/
    zsh_history.<YYYYmmdd-HHMMSS>.<n>   (last 20 kept)
```

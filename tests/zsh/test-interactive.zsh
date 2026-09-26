#!/usr/bin/env zsh
# Drives a real interactive zsh through a pseudo-tty (zsh/zpty) so the
# suggestion widgets run exactly as they would for a real user: this is the
# only reliable way to test them, since a batch script never goes through
# the same per-line history-commit path a live ZLE read/execute loop does.
#
# zpty -w appends a trailing newline UNLESS -n is given, so every send of a
# partial (not-yet-submitted) command buffer below uses -n; only sends that
# should actually submit a line use an explicit trailing \r with -n too, to
# avoid an extra blank Enter from zpty's own auto-appended newline.
source "${0:A:h}/harness.zsh"
zmodload zsh/zpty

PLUGIN="${0:A:h}/../../zsh-smart-suggest.plugin.zsh"
WORK=$(mktemp -d)
ZDOTDIR="$WORK/zdotdir"
mkdir -p "$ZDOTDIR"

cat > "$ZDOTDIR/.zshrc" <<EOF
unsetopt PROMPT_SP
export HISTFILE="$WORK/histfile"
export ZSS_HISTFILE="\$HISTFILE"
export XDG_DATA_HOME="$WORK/data"
SAVEHIST=1000
HISTSIZE=1000
PS1='ZSSPROMPT%% '
setopt no_beep
# So a suggestion word containing a glob character (tested below) can be
# submitted and echoed back literally instead of erroring as "no matches".
setopt nonomatch
# Mirrors what a real install's ~/.zshrc block does (zss/installer.py): PATH
# so ^X^D's \`zss rm\` call finds the CLI instead of failing with "command
# not found", and INC_APPEND_HISTORY so a just-run command is on disk (where
# the separate \`zss\` process reads from) instead of only in this shell's
# in-memory \$history. Before finding 8's fix, either gap was invisible: the
# widget always claimed "removed" no matter how \`zss rm\` actually went.
export PATH="${0:A:h}/../../bin:\$PATH"
setopt INC_APPEND_HISTORY HIST_FCNTL_LOCK
source "$PLUGIN"
EOF

typeset -F SECONDS=0
_zss_pty_read_until() {
  # _zss_pty_read_until <needle> <timeout-seconds>
  local needle=$1 max=$2 chunk
  local -F start=$SECONDS
  buf_captured=""
  while (( SECONDS - start < max )); do
    if zpty -r zsstest chunk 2>/dev/null; then
      buf_captured+=$chunk
      [[ $buf_captured == *${needle}* ]] && return 0
    else
      sleep 0.05
    fi
  done
  return 1
}

zpty -b zsstest "ZDOTDIR=$ZDOTDIR zsh -i"

if _zss_pty_read_until 'ZSSPROMPT%' 10; then
  pass "interactive shell started and showed a prompt"
else
  fail "interactive shell never showed a prompt"
  zpty -d zsstest 2>/dev/null
  rm -rf "$WORK"
  summary
  exit $?
fi

zpty -w -n zsstest $'echo hello world\r'
_zss_pty_read_until 'hello world' 10 >/dev/null
_zss_pty_read_until 'ZSSPROMPT%' 10 >/dev/null

zpty -w -n zsstest 'echo hel'
if _zss_pty_read_until 'lo world' 5; then
  pass "ghost suggestion for a command run moments ago appears while typing"
else
  fail "no ghost suggestion appeared for a just-run command"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi

# --- regression: _zss_fetch must not leak the matched history line to the
# terminal. It used to `print -r -- "$match"` in addition to setting $REPLY;
# since _zss_suggest calls it WITHOUT a `$(...)` subshell (required to fix
# the negative-cache regression, finding 5), that print wrote the full
# matched command straight to the tty, as its own "$match\r\n" line, on
# every keystroke that had a match — distinct from the ghost suggestion,
# which is drawn inline via POSTDISPLAY with no trailing \r\n of its own.
if [[ $buf_captured == *$'echo hello world\r\n'* ]]; then
  fail "_zss_fetch leaked the matched suggestion text into the terminal output"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
else
  pass "_zss_fetch does not leak the matched suggestion text into the terminal output"
fi

# clear the partial line before the next scenario (^U: kill-whole-line)
zpty -w -n zsstest $'\x15'
_zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null

zpty -w -n zsstest $'echo hello world\r'
_zss_pty_read_until 'ZSSPROMPT%' 10 >/dev/null
zpty -w -n zsstest 'ech'
_zss_pty_read_until 'o hello world' 5 >/dev/null
zpty -w -n zsstest $'\x18\x04'  # ^X^D: zss-remove-current
if _zss_pty_read_until 'removed' 5; then
  pass "^X^D reports removal of the current suggestion"
else
  fail "^X^D did not report removing the suggestion"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi

zpty -w -n zsstest $'\x15'
_zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null
zpty -w -n zsstest 'echo hel'
if _zss_pty_read_until 'ZSSPROMPT% echo hel$' 3; then
  pass "removed command is no longer suggested"
else
  # Best-effort: absence is hard to assert via substring alone, so just
  # confirm the OLD suggestion text is gone from the freshly read output.
  if [[ $buf_captured != *'lo world'* ]]; then
    pass "removed command is no longer suggested"
  else
    fail "removed command was still suggested"
  fi
fi

zpty -w -n zsstest $'\x15'
_zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null

# --- finding 2: sourcing the plugin a second time must not break typing ---
# A double-source used to make `_zss_widget_wrap` save `_zss_self_insert`
# itself as "the original self-insert", so every keystroke recursed into
# itself until zsh hit its nested-function limit and swallowed the key.
zpty -w -n zsstest "source '$PLUGIN'"$'\r'
_zss_pty_read_until 'ZSSPROMPT%' 5 >/dev/null
zpty -w -n zsstest $'echo twice\r'
if _zss_pty_read_until 'twice' 5 && [[ $buf_captured != *'maximum nested function level'* ]]; then
  pass "typing still works after sourcing the plugin a second time"
else
  fail "typing broke after a second source (self-insert wrapped itself, finding 2)"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi
_zss_pty_read_until 'ZSSPROMPT%' 5 >/dev/null

# --- finding 3: Right arrow moves one char instead of jumping to end-of-line
# when the cursor isn't at the end (so there is no suggestion to accept) ---
zpty -w -n zsstest 'abcdef'
_zss_pty_read_until 'abcdef' 5 >/dev/null
zpty -w -n zsstest $'\x01'      # ^A: beginning-of-line
zpty -w -n zsstest $'\x1b[C'    # Right arrow
zpty -w -n zsstest $'X\r'
if _zss_pty_read_until 'command not found: aXbcdef' 5; then
  pass "Right arrow moves one character instead of jumping to end-of-line when not at EOL"
else
  fail "Right arrow jumped to end-of-line instead of moving one character (finding 3)"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi
zpty -w -n zsstest $'\x15'
_zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null

# --- finding 4: Alt-f falls back to forward-word when there is no suggestion ---
# zsh's native forward-word lands at the START of the next word (skipping
# the rest of the current one and the space after it), so inserting "X"
# there splits "one two three" into "one" + "Xtwo three": if the fallback
# never ran (the pre-fix no-op), the whole line "oneX two three" would
# stay one word once X lands mid-word instead of at a word boundary, and
# zsh would report `command not found: oneX` rather than `one`.
zpty -w -n zsstest 'one two three'
_zss_pty_read_until 'one two three' 5 >/dev/null
zpty -w -n zsstest $'\x01'      # ^A: beginning-of-line
zpty -w -n zsstest $'\x1bf'     # Alt-f (ESC f)
zpty -w -n zsstest $'X\r'
if _zss_pty_read_until $'found: one\r' 5; then
  pass "Alt-f falls back to forward-word when there is no suggestion"
else
  fail "Alt-f did nothing when there was no suggestion (finding 4)"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi
zpty -w -n zsstest $'\x15'
_zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null

# --- lower-severity: accept-word treats the suggestion word literally, not
# as a glob pattern (a suggestion word like "*.txt" used to make the (i)
# index search match a shorter, wrong span and append the truncated "*.tx") ---
zpty -w -n zsstest $'echo a *.txt foo\r'
_zss_pty_read_until 'ZSSPROMPT%' 5 >/dev/null
zpty -w -n zsstest 'echo a'
_zss_pty_read_until '*.txt foo' 5 >/dev/null
zpty -w -n zsstest $'\x1bf'     # Alt-f (accept-word)
zpty -w -n zsstest $'\r'
if _zss_pty_read_until '*.txt' 5; then
  pass "Alt-f (accept-word) extends by the literal suggestion word, not a glob match"
else
  fail "Alt-f (accept-word) corrupted a glob-containing suggestion word"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi
zpty -w -n zsstest $'\x15'
_zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null

# --- finding 8 (failure path): zss-remove-current must report failure,
# not always claim "removed", when `zss rm` actually fails. Shadow `bin/`
# out of PATH (leaving `zss` unresolvable) so the CLI call fails exactly
# like it would on a lock timeout or a not-found entry.
zpty -w -n zsstest $'echo failcase\r'
_zss_pty_read_until 'ZSSPROMPT%' 5 >/dev/null
zpty -w -n zsstest $'PATH=/usr/bin:/bin\r'
_zss_pty_read_until 'ZSSPROMPT%' 5 >/dev/null
zpty -w -n zsstest 'echo failc'
_zss_pty_read_until 'ase' 5 >/dev/null
zpty -w -n zsstest $'\x18\x04'  # ^X^D: zss-remove-current
if _zss_pty_read_until 'zss: failed to remove' 5; then
  pass "^X^D reports failure instead of claiming success when zss rm fails"
else
  fail "^X^D claimed success even though zss rm failed (finding 8)"
  print -r -- "--- captured output ---"; print -r -- "$buf_captured"; print -r -- "--- end ---"
fi

zpty -d zsstest 2>/dev/null
rm -rf "$WORK"
summary

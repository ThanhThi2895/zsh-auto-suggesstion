#!/usr/bin/env zsh
# Up/Down history navigation in a real interactive zsh (zsh/zpty), with the
# plugin loaded and the options a real install sets.
#
# Regression: the plugin's cross-terminal history reload (`fc -RI`) used to
# run from precmd, where it desynced zsh's history counter from the pending
# line's history number, so up-line-or-history found no entry to move from
# and Up/Down did nothing. With INC_APPEND_HISTORY that happened at every
# prompt after the first command, and at the very first prompt whenever the
# history file changed between the plugin being sourced and that prompt.
source "${0:A:h}/harness.zsh"
zmodload zsh/zpty

PLUGIN=${ZSS_TEST_PLUGIN:-"${0:A:h}/../../zsh-smart-suggest.plugin.zsh"}
WORK=$(mktemp -d)
ZDOTDIR="$WORK/zdotdir"
mkdir -p "$ZDOTDIR"
print -r -- ': 1700000000:0;echo first_old' > "$WORK/histfile"
print -r -- ': 1700000001:0;echo second_old' >> "$WORK/histfile"

cat > "$ZDOTDIR/.zshrc" <<EOF
unsetopt PROMPT_SP
HISTFILE="$WORK/histfile"
export XDG_DATA_HOME="$WORK/data"
HISTSIZE=50000
SAVEHIST=50000
PS1='ZSSPROMPT%% '
setopt no_beep INC_APPEND_HISTORY HIST_FCNTL_LOCK
source "$PLUGIN"
# Another terminal appending to the shared history after the plugin loaded
# but before this shell's first prompt (the mtime check has 1s resolution).
sleep 1.1
print -r -- ': 1700000002:0;echo from_other_term' >> "\$HISTFILE"
# Test-only probe (^Xq): appends the live ZLE state to a file.
_zss_test_dump() {
  print -r -- "BUFFER=[\$BUFFER]" >> "$WORK/zle-state"
}
zle -N _zss_test_dump
bindkey '^Xq' _zss_test_dump
EOF

typeset -F SECONDS=0
_zss_pty_read_until() {
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

_zss_dump_after() {
  # _zss_dump_after <keys>: sends <keys> then ^Xq, sets $REPLY to the
  # BUFFER the probe recorded, then abandons the line with ^G (send-break).
  # Not ^U: that only empties the buffer and leaves ZLE on the recalled
  # history entry, so the next Up would start from there instead of from a
  # fresh prompt.
  : >| "$WORK/zle-state"
  zpty -w -n zsstest "$1"$'\x18q'
  local _i
  for _i in {1..60}; do
    [[ -s "$WORK/zle-state" ]] && break
    sleep 0.05
  done
  REPLY=$(<"$WORK/zle-state")
  zpty -w -n zsstest $'\x07'
  _zss_pty_read_until 'ZSSPROMPT%' 3 >/dev/null
}

zpty -b zsstest "ZDOTDIR=$ZDOTDIR zsh -i"
if ! _zss_pty_read_until 'ZSSPROMPT%' 10; then
  fail "interactive shell never showed a prompt"
  zpty -d zsstest 2>/dev/null
  rm -rf "$WORK"
  summary
  exit $?
fi

_zss_dump_after $'\e[A'
assert_eq "$REPLY" "BUFFER=[echo from_other_term]" \
  "Up at the first prompt recalls history after the histfile changed during startup"

_zss_run() {
  # _zss_run <command>: submits <command> and waits for the next prompt. The
  # sleep first moves the clock past the histfile's last mtime second, so
  # the INC_APPEND_HISTORY write is always seen as a change: the plugin's
  # check has one-second resolution, and a same-second write would skip the
  # reload this test needs to exercise.
  sleep 1.1
  zpty -w -n zsstest "$1"$'\r'
  _zss_pty_read_until 'ZSSPROMPT%' 5 >/dev/null
}

# Each probe below runs at the first prompt after a command: that prompt is
# where the reload happens (a ^G'd prompt after it has no mtime change).
_zss_run 'echo hi'
_zss_dump_after $'\e[A'
assert_eq "$REPLY" "BUFFER=[echo hi]" \
  "Up after running a command (INC_APPEND_HISTORY) recalls that command"

_zss_run 'echo two'
_zss_dump_after $'\eOA\eOA'
assert_eq "$REPLY" "BUFFER=[echo hi]" \
  "Up (application-mode \\eOA) twice walks back through history"

_zss_run 'echo three'
_zss_dump_after $'\e[A\e[A\e[B'
assert_eq "$REPLY" "BUFFER=[echo three]" \
  "Down walks forward again after Up"

# Cross-terminal pickup still works: a command another terminal appends is
# imported after this shell runs its next command. The import runs after
# this shell saved "echo four", so the imported entry is the newest one.
print -r -- ': 1700000003:0;echo zzz_from_elsewhere' >> "$WORK/histfile"
_zss_run 'echo four'
_zss_dump_after $'\e[A\e[A'
assert_eq "$REPLY" "BUFFER=[echo four]" \
  "Up still walks history after a cross-terminal reload"

zpty -w -n zsstest 'echo zzz'
if _zss_pty_read_until '_from_elsewhere' 5; then
  pass "a command appended by another terminal is still suggested"
else
  fail "a command appended by another terminal was not suggested"
fi

zpty -d zsstest 2>/dev/null
rm -rf "$WORK"
summary

#!/usr/bin/env zsh
# zsh -f test-perf.zsh
source "${0:A:h}/harness.zsh"
zmodload zsh/datetime

WORK=$(mktemp -d)
export XDG_DATA_HOME="$WORK/data"
export HISTFILE="$WORK/histfile"
export ZSS_HISTFILE="$HISTFILE"
SAVEHIST=60000
HISTSIZE=60000

source "${0:A:h}/../../zsh-smart-suggest.plugin.zsh"

integer i
for (( i = 1; i <= 50000; i++ )); do
  print -s "command number $i --flag=value"
done
print -s "__sentinel__"

local t0 t1

t0=$EPOCHREALTIME
_zss_fetch "command number 4999" >/dev/null
t1=$EPOCHREALTIME
local elapsed_ms=$(( (t1 - t0) * 1000 ))
print -r -- "_zss_fetch (match near newest) on 50k entries took ${elapsed_ms}ms"
if (( elapsed_ms < 5 )); then
  pass "_zss_fetch under 5ms on 50k history entries, match case (${elapsed_ms}ms)"
else
  fail "_zss_fetch too slow on 50k history entries, match case (${elapsed_ms}ms, want < 5ms)"
fi

t0=$EPOCHREALTIME
_zss_fetch "zzz-never-run-before" >/dev/null
t1=$EPOCHREALTIME
elapsed_ms=$(( (t1 - t0) * 1000 ))
print -r -- "_zss_fetch (cold, no match) on 50k entries took ${elapsed_ms}ms"
if (( elapsed_ms < 10 )); then
  pass "_zss_fetch under 10ms on 50k history entries, cold no-match case (${elapsed_ms}ms)"
else
  fail "_zss_fetch too slow on 50k history entries, cold no-match case (${elapsed_ms}ms, want < 10ms)"
fi

# Typing further into a buffer that already had no match anywhere in
# history must not re-scan: the negative-match cache should make this
# effectively instant regardless of history size.
t0=$EPOCHREALTIME
_zss_fetch "zzz-never-run-before-either" >/dev/null
t1=$EPOCHREALTIME
elapsed_ms=$(( (t1 - t0) * 1000 ))
print -r -- "_zss_fetch (warm, extends cached no-match prefix) took ${elapsed_ms}ms"
if (( elapsed_ms < 1 )); then
  pass "_zss_fetch negative-match cache keeps extending a no-match buffer under 1ms (${elapsed_ms}ms)"
else
  fail "_zss_fetch negative-match cache did not keep the buffer fast (${elapsed_ms}ms, want < 1ms)"
fi

# Regression for finding 5: _zss_suggest used to call `_zss_fetch` through
# `match=$(_zss_fetch "$BUFFER")`, a command substitution that runs in a
# subshell — so every `_zss_no_match_prefix` assignment inside _zss_fetch
# was thrown away the moment that subshell exited, silently disabling the
# cache on every real keystroke even though the direct calls above (and in
# tests/zsh/test-suggest.zsh) show it working. Drive _zss_suggest itself
# (BUFFER/CURSOR are the same plain globals real typing uses) to prove the
# cache survives through the actual call site, not just in isolation.
BUFFER="zzz-suggest-path-never-run"
CURSOR=${#BUFFER}
t0=$EPOCHREALTIME
_zss_suggest
t1=$EPOCHREALTIME
elapsed_ms=$(( (t1 - t0) * 1000 ))
print -r -- "_zss_suggest (cold, no match) on 50k entries took ${elapsed_ms}ms"

BUFFER="zzz-suggest-path-never-run-either"
CURSOR=${#BUFFER}
t0=$EPOCHREALTIME
_zss_suggest
t1=$EPOCHREALTIME
elapsed_ms=$(( (t1 - t0) * 1000 ))
print -r -- "_zss_suggest (warm, extends cached no-match prefix) took ${elapsed_ms}ms"
if (( elapsed_ms < 1 )); then
  pass "_zss_suggest (the real self-insert call path) keeps the negative-match cache warm (${elapsed_ms}ms)"
else
  fail "_zss_suggest did not benefit from the negative-match cache (${elapsed_ms}ms, want < 1ms) — the subshell call may be back"
fi

rm -rf "$WORK"
summary

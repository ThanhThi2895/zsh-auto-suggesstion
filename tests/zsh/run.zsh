#!/usr/bin/env zsh
# Runs every tests/zsh/test-*.zsh file with `zsh -f` and reports a summary.
emulate -L zsh
local dir=${0:A:h}
typeset -i total_fail=0
local f
for f in "$dir"/test-*.zsh; do
  [[ -e $f ]] || continue
  print -r -- "== ${f:t} =="
  zsh -f "$f"
  if (( $? != 0 )); then
    total_fail+=1
  fi
done
if (( total_fail > 0 )); then
  print -r -- "$total_fail test file(s) failed"
  exit 1
fi
print -r -- "all zsh tests passed"

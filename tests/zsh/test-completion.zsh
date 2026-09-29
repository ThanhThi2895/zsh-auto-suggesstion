#!/usr/bin/env zsh
# Tab completion vs. the ghost suggestion, driven through a real pty like
# test-interactive.zsh. Completion deletes and re-inserts the word under the
# cursor even when it only lists candidates, which collapses region_highlight
# spans: the ghost's text then rendered in the normal colour, as if typed.
source "${0:A:h}/harness.zsh"
zmodload zsh/zpty

PLUGIN="${0:A:h}/../../zsh-smart-suggest.plugin.zsh"
WORK=$(mktemp -d)
ZDOTDIR="$WORK/zdotdir"
mkdir -p "$ZDOTDIR" "$WORK/bin"
# Several commands starting with "git", so Tab lists instead of completing.
for _c in git git-lfs gitleaks; do
  print '#!/bin/sh' > "$WORK/bin/$_c"
  chmod +x "$WORK/bin/$_c"
done
print -r -- ': 1:0;git fetch --all -p' > "$WORK/histfile"

cat > "$ZDOTDIR/.zshrc" <<EOF
unsetopt PROMPT_SP
setopt no_beep extended_history
export HISTFILE="$WORK/histfile"
export ZSS_HISTFILE="\$HISTFILE"
export XDG_DATA_HOME="$WORK/data"
SAVEHIST=1000
HISTSIZE=1000
PATH="$WORK/bin:/bin:/usr/bin"
PS1='ZSSPROMPT%% '
[[ -n \$ZSS_TEST_MENU ]] && zstyle ':completion:*' menu select
source "$PLUGIN"
# compinit AFTER the plugin on purpose: it re-creates every completion
# widget, which must not undo the plugin's completion handling.
autoload -Uz compinit && compinit -u -D
_zss_test_dump() {
  print -r -- "BUFFER=[\$BUFFER] POSTDISPLAY=[\$POSTDISPLAY] RH=[\${(j:|:)region_highlight}]" >> "$WORK/zle-state"
}
zle -N _zss_test_dump
bindkey '^Xq' _zss_test_dump
EOF

typeset -F SECONDS=0
_zss_pty_drain() {
  # _zss_pty_drain <seconds>: collect everything the shell prints meanwhile.
  local chunk
  local -F start=$SECONDS
  buf_captured=""
  while (( SECONDS - start < $1 )); do
    if zpty -r zsstest chunk 2>/dev/null; then
      buf_captured+=$chunk
    else
      sleep 0.05
    fi
  done
}

_zss_wait_states() {
  local _i
  for _i in {1..40}; do
    [[ -f $WORK/zle-state ]] && (( $(wc -l < "$WORK/zle-state") >= $1 )) && return
    sleep 0.05
  done
}

# --- Tab that only lists candidates keeps the ghost (and syntax) spans ---
zpty -b zsstest "ZDOTDIR=$ZDOTDIR TERM=xterm-256color zsh -i"
_zss_pty_drain 2
zpty -w -n zsstest 'git'
_zss_pty_drain 1
zpty -w -n zsstest $'\t'
_zss_pty_drain 1.5
if [[ $buf_captured == *gitleaks* ]]; then
  pass "Tab after 'git' lists several completion candidates"
else
  fail "Tab after 'git' did not list candidates (got: ${(q+)buf_captured})"
fi
zpty -w -n zsstest $'\x18q'
_zss_wait_states 1
_state=$(<"$WORK/zle-state")
_rh="|${${_state#*RH=\[}%\]}|"
if [[ $_state == 'BUFFER=[git] POSTDISPLAY=[ fetch --all -p] '* \
   && $_rh == *'|3 18 fg=8 memo=zss|'* ]]; then
  pass "ghost keeps its suggestion highlight after a listing Tab"
else
  fail "ghost lost its highlight span after a listing Tab (got: '$_state')"
fi
if [[ $_rh == *'|0 3 fg=green memo=zss-hl|'* ]]; then
  pass "syntax highlight of the typed word survives a listing Tab"
else
  fail "syntax highlight of the typed word was lost after a listing Tab (got: '$_state')"
fi
zpty -d zsstest 2>/dev/null

# --- menu selection: no pre-redraw hook runs inside the menu's own loop,
# so the ghost must be hidden while the menu is open, then come back.
: >| "$WORK/zle-state"
zpty -b zsstest "ZDOTDIR=$ZDOTDIR TERM=xterm-256color ZSS_TEST_MENU=1 zsh -i"
_zss_pty_drain 2
zpty -w -n zsstest 'git'
_zss_pty_drain 1
zpty -w -n zsstest $'\t'
_zss_pty_drain 1
zpty -w -n zsstest $'\t'   # second Tab enters menu selection
_zss_pty_drain 1.5
if [[ $buf_captured == *$'\e[7mgit'* ]]; then
  pass "second Tab enters menu selection"
else
  fail "second Tab did not enter menu selection (got: ${(q+)buf_captured})"
fi
# The menu's redraw of the command line must end right after "git" (an SGR
# reset may follow its syntax colour), with no ghost text anywhere.
if [[ ${buf_captured##*$'\e[24m'} == *'git'(|$'\e[39m')$'\e[K' \
   && $buf_captured != *'fetch --all -p'* ]]; then
  pass "ghost text is hidden while the completion menu is open"
else
  fail "ghost text drawn as plain text inside the completion menu (got: ${(q+)buf_captured})"
fi
# The menu's own redraw of the command line (after the listing) must keep
# the typed command's syntax colour.
if [[ ${buf_captured##*$'\e[24m'} == *$'\e[32mgit'* ]]; then
  pass "typed command keeps its syntax highlight while the menu is open"
else
  fail "typed command lost its syntax highlight inside the completion menu (got: ${(q+)buf_captured})"
fi
zpty -w -n zsstest $'\x18q'   # leaves the menu, then dumps
_zss_pty_drain 1
_zss_wait_states 1
_state=$(<"$WORK/zle-state")
_rh="|${${_state#*RH=\[}%\]}|"
if [[ $_state == *'POSTDISPLAY=[fetch --all -p]'* && $_rh == *'|4 18 fg=8 memo=zss|'* ]]; then
  pass "ghost suggestion comes back highlighted after leaving the menu"
else
  fail "ghost suggestion missing or unhighlighted after leaving the menu (got: '$_state')"
fi
zpty -d zsstest 2>/dev/null

rm -rf "$WORK"
summary

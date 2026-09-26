#!/usr/bin/env zsh
# zsh -f test-suggest.zsh
source "${0:A:h}/harness.zsh"

WORK=$(mktemp -d)
export XDG_DATA_HOME="$WORK/data"
export HISTFILE="$WORK/histfile"
export ZSS_HISTFILE="$HISTFILE"
SAVEHIST=1000
HISTSIZE=1000

source "${0:A:h}/../../zsh-smart-suggest.plugin.zsh"

print -s "git status"
print -s "git commit -m test"
print -s "echo hello world"
print -s "ls -la ~/"
print -s "echo special [chars] (test) *glob*"
# zsh keeps the most-recently-added history event mutable/invisible in
# $history until another history event starts (this is what lets an
# interactively-typed line still edit its own about-to-be-saved history
# entry); a harmless sentinel finalizes the real entries above so this
# batch-mode test can see all of them via `$history` too.
print -s "__zss_test_sentinel__"

REPLY=""
_zss_fetch "git"
assert_eq "$REPLY" "git commit -m test" "newest match wins"

REPLY=""
if _zss_fetch "zzz-nomatch-xyz"; then
  fail "expected no match for zzz-nomatch-xyz"
else
  pass "no match returns nonzero"
fi

_zss_block_rules=("git commit")
REPLY=""
_zss_fetch "git"
assert_eq "$REPLY" "git status" "blocked-prefix newest entry is skipped"
# Rules are literal prefixes even under the user's GLOB_SUBST (zshaddhistory
# calls _zss_is_blocked with the user's options, not the plugin's).
_zss_block_rules=("rm [ab]")
setopt GLOB_SUBST
if _zss_is_blocked "rm a"; then fail "rule matched as a glob under GLOB_SUBST"; else pass "rule not treated as a glob under GLOB_SUBST"; fi
if _zss_is_blocked "rm [ab] x"; then pass "literal rule prefix blocked under GLOB_SUBST"; else fail "literal rule prefix not blocked under GLOB_SUBST"; fi
unsetopt GLOB_SUBST
_zss_block_rules=()

_zss_block_rules=()

locally_deleted_cmd="git commit -m test"
_zss_locally_deleted[$locally_deleted_cmd]=1
REPLY=""
_zss_fetch "git"
assert_eq "$REPLY" "git status" "locally-deleted newest entry is skipped"
unset "_zss_locally_deleted[$locally_deleted_cmd]"

ZSS_IGNORE_PATTERN="ls *"
REPLY=""
_zss_fetch "ls -la ~/"
if [[ -n $REPLY ]]; then
  fail "expected ZSS_IGNORE_PATTERN to suppress suggestion, got: $REPLY"
else
  pass "ZSS_IGNORE_PATTERN suppresses match"
fi
ZSS_IGNORE_PATTERN=""

REPLY=""
_zss_fetch "echo special [chars]"
assert_eq "$REPLY" "echo special [chars] (test) *glob*" "special glob chars in buffer treated literally"

REPLY=""
_zss_fetch ""
if [[ -n $REPLY ]]; then
  fail "expected empty buffer to yield no suggestion"
else
  pass "empty buffer yields no suggestion"
fi

long_buf=$(printf 'a%.0s' {1..400})
REPLY=""
if _zss_fetch "$long_buf"; then
  fail "expected buffer over ZSS_MAX_BUFFER to be skipped"
else
  pass "buffer over ZSS_MAX_BUFFER is skipped"
fi

# --- regression: typing a character that breaks the match must clear the
# ghost text. _zss_render used to return early on an empty suggestion
# without touching POSTDISPLAY, so "git c" + ghost "ommit -m test" became
# "git cl" + a stale, un-highlighted "ommit -m test" that read as typed text.
# (Outside ZLE, BUFFER/CURSOR/POSTDISPLAY/region_highlight are plain
# parameters, so the render path can be driven directly.)
region_highlight=()
BUFFER="git c"; CURSOR=${#BUFFER}
_zss_suggest
assert_eq "$POSTDISPLAY" "ommit -m test" "ghost text is the rest of the matched suggestion"
assert_eq "${(M)region_highlight:#*memo=zss}" "5 18 fg=8 memo=zss" "ghost highlight covers exactly the ghost text"
BUFFER="git cl"; CURSOR=${#BUFFER}
_zss_suggest
assert_eq "$POSTDISPLAY" "" "a non-matching keystroke clears the stale ghost text"
assert_eq "$ZSS_SUGGESTION" "" "a non-matching keystroke clears the suggestion"
assert_eq "${#${(M)region_highlight:#*memo=zss}}" "0" "a non-matching keystroke drops the ghost highlight"

# A suggestion that no longer extends the buffer is never rendered as ghost text.
BUFFER="git cl"; ZSS_SUGGESTION="git commit -m test"; POSTDISPLAY="ommit -m test"
_zss_render
assert_eq "$POSTDISPLAY" "" "a suggestion that doesn't extend the buffer is not rendered"

# The ghost span must not depend on the user's options: under GLOB_SUBST,
# stripping "$BUFFER" as a pattern made "echo special [" match nothing and
# drew the whole suggestion (and a too-long highlight) after the buffer.
setopt GLOB_SUBST
BUFFER="echo special ["; CURSOR=${#BUFFER}
_zss_suggest
unsetopt GLOB_SUBST
assert_eq "$POSTDISPLAY" "chars] (test) *glob*" "ghost text is exact under GLOB_SUBST"
assert_eq "${(M)region_highlight:#*memo=zss}" "14 34 fg=8 memo=zss" "ghost highlight span is exact under GLOB_SUBST"

rm -rf "$WORK"
summary

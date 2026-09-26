#!/usr/bin/env zsh
# zsh -f test-highlight.zsh
source "${0:A:h}/harness.zsh"
zmodload zsh/datetime

WORK=$(mktemp -d)
export XDG_DATA_HOME="$WORK/data"
export HISTFILE="$WORK/histfile"
export ZSS_HISTFILE="$HISTFILE"
SAVEHIST=1000
HISTSIZE=1000

source "${0:A:h}/../../zsh-smart-suggest.plugin.zsh"
alias ll="ls -l"
setopt interactivecomments

assert_tokens() {
  # assert_tokens <label> <buffer> <expected-tokens-newline-separated>
  local label=$1 buf=$2 expected=$3
  local got
  got=$(_zss_hl_tokens "$buf")
  assert_eq "$got" "$expected" "$label"
}

assert_tokens "ls -la ~/" "ls -la ~/" $'0 2 command\n3 6 option\n7 9 path'

assert_tokens "unknown command" "nosuchcmd foo" "0 9 unknown"

assert_tokens "alias" "ll" "0 2 alias"

assert_tokens "builtin" "cd" "0 2 builtin"

assert_tokens "if/then/fi with quoted string" \
  'if true; then echo "a b"; fi' \
  $'0 2 reserved\n3 7 builtin\n7 8 operator\n9 13 reserved\n14 18 builtin\n19 24 string\n24 25 operator\n26 28 reserved'

assert_tokens "assignment, precommand, pipe, redirects" \
  'FOO=1 sudo git status | grep -v x > /tmp/o 2>&1' \
  $'0 5 assignment\n6 10 command\n11 14 command\n22 23 operator\n24 28 command\n29 31 option\n34 35 redirect\n43 46 redirect'

assert_tokens "variables and single-quoted string" \
  'echo $HOME ${PATH} '"'"'lit $x'"'"'' \
  $'0 4 builtin\n5 10 variable\n11 18 variable\n19 27 string'

assert_tokens "unterminated string" 'echo "unterminated' $'0 4 builtin\n5 18 string'

assert_tokens "glob and trailing comment" 'print *.zsh # note' $'0 5 builtin\n6 11 glob\n12 18 comment'

assert_tokens "multi-line backslash-newline buffer" $'echo foo \\\nbar' "0 4 builtin"

assert_tokens "vietnamese text in a string (character offsets)" 'echo "xin chào"' $'0 4 builtin\n5 15 string'

# --- `~name` for a nonexistent user/named directory must not crash the scan ---
# `${~w}` on `~nosuchuser` raises a fatal "no such user or named directory"
# zsh error (not a suppressible glob-nomatch), which used to abort the whole
# highlight scan mid-redraw. Both the argument-position and command-position
# classifiers must skip the stat instead of expanding it.
assert_tokens "nonexistent named user/directory in argument position does not crash" \
  "ls ~zss_test_nonexistent_user_xyz" $'0 2 command'

assert_tokens "nonexistent named user/directory in command position does not crash" \
  "~zss_test_nonexistent_user_xyz/bin/foo -x" $'0 38 unknown\n39 41 option'

# --- region_highlight ownership: only memo=zss-hl entries are touched ---
region_highlight=("0 2 fg=red memo=other-plugin")
BUFFER="ls -la"
_zss_hl_redraw
local -a hl_entries
hl_entries=("${(M)region_highlight:#*memo=zss-hl}")
local -a foreign_entries
foreign_entries=("${(M)region_highlight:#*memo=other-plugin}")
assert_true $(( ${#hl_entries} > 0 )) "redraw adds memo=zss-hl entries"
assert_true $(( ${#foreign_entries} == 1 )) "a foreign region_highlight entry survives a redraw"

# --- colors.conf: invalid lines ignored, valid lines override, mtime reload ---
mkdir -p "$XDG_DATA_HOME/zsh-smart-suggest"
colors_file="$XDG_DATA_HOME/zsh-smart-suggest/colors.conf"
print -r -- "command=fg=42" > "$colors_file"
print -r -- "bogus line with no equals" >> "$colors_file"
print -r -- "command=fg=not-a-color" >> "$colors_file"
_zss_hl_load_config
assert_eq "$ZSS_HL_STYLES[command]" "fg=42" "valid colors.conf line overrides default"

sleep 1.1
print -r -- "alias=bg=blue" >> "$colors_file"
_zss_refresh
assert_eq "$ZSS_HL_STYLES[alias]" "bg=blue" "colors.conf mtime change triggers reload via _zss_refresh"

# --- timing: 200-char command line under 3ms ---
long_cmd="echo aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee ffffffffff gggggggggg hhhhhhhhhh iiiiiiiiii jjjjjjjjjj kkkkkkkkkk llllllllll mmmmmmmmmm nnnnnnnnnn oooooooooo pppppppppp qqqqqqqqqq rrrrrrrrrr"
if (( ${#long_cmd} < 195 || ${#long_cmd} > 205 )); then
  fail "test buffer for timing check is not ~200 chars (${#long_cmd})"
fi
local t0 t1
t0=$EPOCHREALTIME
_zss_hl_tokens "$long_cmd" >/dev/null
t1=$EPOCHREALTIME
local elapsed_ms=$(( (t1 - t0) * 1000 ))
print -r -- "_zss_hl_tokens on a ${#long_cmd}-char line took ${elapsed_ms}ms"
if (( elapsed_ms < 3 )); then
  pass "_zss_hl_tokens under 3ms on a 200-char line (${elapsed_ms}ms)"
else
  fail "_zss_hl_tokens too slow on a 200-char line (${elapsed_ms}ms, want < 3ms)"
fi

rm -rf "$WORK"
summary

# zsh-smart-suggest: inline history suggestions + syntax highlighting.
#
# Config (set before sourcing, or any time — read live):
#   ZSS_HIGHLIGHT_STYLE   region_highlight style for the ghost suggestion; if unset,
#                         uses colors.conf's `suggestion` key / `zss colors` (default: fg=8)
#   ZSS_MAX_BUFFER        skip suggesting above this many characters (default: 300)
#   ZSS_IGNORE_PATTERN    zsh glob pattern; matching buffers never get a suggestion (default: none)
#   ZSS_DISABLE           set to 1 to disable suggestions entirely
#
# Commands: `zss-toggle` flips suggestions on/off for the current shell.

0=${(%):-%N}

# Re-sourcing this file (e.g. a second `source ~/.zshrc`) must not re-wrap
# widgets that are already wrapped: `_zss_widget_wrap` saves whatever
# `self-insert` currently points to as "the original" to fall back to, and
# on a second source that would be `_zss_self_insert` itself, making every
# keystroke recurse into itself until zsh hits its nested-function limit.
if (( ${+_ZSS_LOADED} )); then
  return
fi
typeset -g _ZSS_LOADED=1

typeset -g ZSS_PLUGIN_DIR=${0:A:h}

zmodload zsh/parameter
zmodload zsh/stat
autoload -Uz add-zle-hook-widget

# ZSS_HIGHLIGHT_STYLE is intentionally left unset here (not defaulted) so
# _zss_render can tell "user set it explicitly" apart from "fall back to
# colors.conf's `suggestion` style" — an explicit value always wins.
: ${ZSS_MAX_BUFFER:=300}
: ${ZSS_IGNORE_PATTERN:=}
: ${ZSS_DISABLE:=0}

typeset -g ZSS_SUGGESTION=""
typeset -ga _zss_block_rules
typeset -gA _zss_locally_deleted
typeset -g _zss_last_hist_mtime=0
typeset -g _zss_last_hist_count=0
typeset -g _zss_last_block_mtime=0
typeset -g _zss_last_colors_mtime=0
typeset -g _zss_no_match_prefix=""

# ---------------------------------------------------------------------------
# Paths (mirrors zss/store.py's paths(); ZSS_HISTFILE lets tests override).
# ---------------------------------------------------------------------------

_zss_data_dir() {
  print -r -- "${XDG_DATA_HOME:-$HOME/.local/share}/zsh-smart-suggest"
}

_zss_blocklist_file() {
  print -r -- "$(_zss_data_dir)/blocklist.txt"
}

_zss_histfile() {
  print -r -- "${ZSS_HISTFILE:-${HISTFILE:-$HOME/.zsh_history}}"
}

# ---------------------------------------------------------------------------
# Block list: plain UTF-8 lines, backslash-newline continuation for embedded
# real newlines (matches zss/history.py's encode_block_rule/decode_block_rule).
# Each line is a PREFIX rule, added manually only (CLI/TUI/web "Block"
# action) — deleting history entries never adds one.
# ---------------------------------------------------------------------------

_zss_load_block_rules() {
  _zss_block_rules=()
  local f=$(_zss_blocklist_file)
  [[ -f $f ]] || return
  local -a physical
  physical=("${(@f)$(<$f)}")
  local rec="" line
  for line in "${physical[@]}"; do
    if [[ -n $rec ]]; then
      rec+=$'\n'"$line"
    else
      rec="$line"
    fi
    if [[ $rec == *'\' ]]; then
      rec="${rec%\\}"
      continue
    fi
    [[ -n $rec ]] && _zss_block_rules+=("$rec")
    rec=""
  done
  [[ -n $rec ]] && _zss_block_rules+=("$rec")
}

_zss_is_blocked() {
  # Prefix match: a command is blocked if it STARTS WITH any rule, no matter
  # what follows. Plain substring comparison (no glob expansion), so a rule
  # containing pattern characters (e.g. "rm -rf *") is matched literally.
  # emulate: this runs from zshaddhistory with the user's options, where
  # e.g. GLOB_SUBST or KSH_ARRAYS would change the comparison below.
  emulate -L zsh
  local cmd=$1 rule
  for rule in "${_zss_block_rules[@]}"; do
    [[ ${cmd[1,${#rule}]} == "$rule" ]] && return 0
  done
  return 1
}

_zss_is_locally_deleted() {
  # Exact-match, in-memory-only, per-shell cache of commands `zss-remove-
  # current` (^X^D) just deleted from THIS shell's history: `fc -RI` (used
  # by _zss_refresh below to pick up cross-terminal history changes) only
  # ever ADDS entries it hasn't seen yet — it can't drop one this shell
  # already loaded into $history, even after it's gone from the histfile.
  # Without this, deleting the suggestion you're looking at would leave it
  # reappearing from the stale in-memory copy on the very next keystroke.
  # It is intentionally separate from the block list: deleting must not
  # block a command from being suggested/saved again, only stop it from
  # reappearing here-and-now — running it again (below, in
  # _zss_zshaddhistory) clears the entry so it can be suggested normally.
  (( ${+_zss_locally_deleted[$1]} ))
}

# ---------------------------------------------------------------------------
# Freshness: reload $history / block list / colors.conf when their mtimes
# change, so a command run in terminal A shows up as a suggestion in B.
# ---------------------------------------------------------------------------

_zss_colors_file() {
  print -r -- "$(_zss_data_dir)/colors.conf"
}

_zss_refresh() {
  local hf=$(_zss_histfile) bf=$(_zss_blocklist_file) cf=$(_zss_colors_file)
  local hist_mtime=0 block_mtime=0 colors_mtime=0
  local -A st
  if zstat -H st -- "$hf" 2>/dev/null; then
    hist_mtime=${st[mtime]}
  fi
  if (( hist_mtime != _zss_last_hist_mtime )); then
    fc -RI 2>/dev/null
    _zss_last_hist_mtime=$hist_mtime
    _zss_no_match_prefix=""
  fi
  # A command run in THIS shell lands in $history immediately, with no
  # histfile write (and so no mtime change above) until the shell saves it —
  # which, without INC_APPEND_HISTORY, can be as late as shell exit. Without
  # this, the negative-match cache above would keep reporting "no match" for
  # a prefix of the command the user just ran, now that _zss_fetch is called
  # without a subshell and its cache assignments actually persist.
  if (( ${#history} != _zss_last_hist_count )); then
    _zss_last_hist_count=${#history}
    _zss_no_match_prefix=""
  fi
  if zstat -H st -- "$bf" 2>/dev/null; then
    block_mtime=${st[mtime]}
  fi
  if (( block_mtime != _zss_last_block_mtime )); then
    _zss_load_block_rules
    _zss_last_block_mtime=$block_mtime
    _zss_no_match_prefix=""
  fi
  if (( ${+functions[_zss_hl_refresh_colors]} )); then
    if zstat -H st -- "$cf" 2>/dev/null; then
      colors_mtime=${st[mtime]}
    fi
    if (( colors_mtime != _zss_last_colors_mtime )); then
      _zss_hl_refresh_colors
      _zss_last_colors_mtime=$colors_mtime
    fi
  fi
}

# ---------------------------------------------------------------------------
# Suggestion search: newest-first scan of the in-memory $history values.
# ---------------------------------------------------------------------------

_zss_fetch() {
  # Sets $REPLY to the match on success. Must NOT print it: _zss_suggest
  # calls this WITHOUT a `$(...)` subshell (that subshell is what discarded
  # every `_zss_no_match_prefix` assignment below, silently disabling the
  # negative-match cache on every real keystroke), and inside a ZLE widget
  # stdout is the tty, so a `print` here would leak the suggestion onto the
  # screen on every keystroke.
  emulate -L zsh
  setopt extendedglob
  local buf=$1
  local buflen=${#buf}
  REPLY=""
  (( buflen == 0 )) && return 1
  (( buflen > ZSS_MAX_BUFFER )) && return 1
  [[ -n $ZSS_IGNORE_PATTERN && $buf == ${~ZSS_IGNORE_PATTERN} ]] && return 1

  # Negative cache: if a shorter prefix of this buffer already had no match
  # anywhere in history, no longer buffer extending it can match either
  # (any entry starting with the longer buffer would also start with the
  # shorter one) — skip the expensive scan below entirely in that case.
  # This is what keeps typing into "new" text (never run before) fast at
  # every keystroke past the first, even on a 50k-entry history.
  if [[ -n $_zss_no_match_prefix && $buf == ${_zss_no_match_prefix}* ]]; then
    return 1
  fi

  # `${history[(r)pattern]}` is a native (C-level) reverse-ordered
  # first-match search over $history's values — dramatically faster at
  # scale than materializing `${(v)history}` and looping, or `:#`
  # filtering (which computes every match, not just the first).
  local escaped=${(b)buf}
  local match=${history[(r)${~escaped}*]}
  if [[ -z $match ]]; then
    _zss_no_match_prefix=$buf
    return 1
  fi
  if ! _zss_is_blocked "$match" && ! _zss_is_locally_deleted "$match"; then
    _zss_no_match_prefix=""
    REPLY=$match
    return 0
  fi

  # Rare path: the nearest match is blocked (or was just deleted in this
  # shell). Fall back to a full scan so it doesn't shadow the next-best
  # real suggestion.
  local -a hist_values
  hist_values=(${(v)history})
  local cmd
  for cmd in "${hist_values[@]}"; do
    (( ${#cmd} <= buflen )) && continue
    [[ ${cmd[1,buflen]} == $buf ]] || continue
    _zss_is_blocked "$cmd" && continue
    _zss_is_locally_deleted "$cmd" && continue
    _zss_no_match_prefix=""
    REPLY=$cmd
    return 0
  done
  _zss_no_match_prefix=$buf
  return 1
}

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_zss_clear_region_highlight() {
  local -a kept
  local entry
  for entry in "${region_highlight[@]}"; do
    [[ $entry == *"memo=zss"[^-]* || $entry == *"memo=zss" ]] && continue
    kept+=("$entry")
  done
  region_highlight=("${kept[@]}")
}

_zss_clear_suggestion() {
  ZSS_SUGGESTION=""
  POSTDISPLAY=""
  _zss_clear_region_highlight
}

_zss_render() {
  # emulate: the widgets run with the user's options, where e.g. GLOB_SUBST
  # would turn $BUFFER into a pattern or KSH_ARRAYS would shift subscripts,
  # making the ghost text (and so its highlight span) the wrong length.
  emulate -L zsh
  _zss_clear_region_highlight
  # No suggestion (or one that no longer extends the buffer) must also wipe
  # POSTDISPLAY: leaving the previous ghost text there with its highlight
  # removed made it look like typed text, e.g. "git c" + "onfig user.email"
  # turning into "git clonfig user.email" once "l" found no match.
  if [[ -z $ZSS_SUGGESTION || ${ZSS_SUGGESTION[1,${#BUFFER}]} != "$BUFFER" ]]; then
    _zss_clear_suggestion
    return
  fi
  local rest=${ZSS_SUGGESTION[${#BUFFER}+1,-1]}
  local style=${ZSS_HIGHLIGHT_STYLE:-${ZSS_HL_STYLES[suggestion]:-fg=8}}
  POSTDISPLAY=$rest
  region_highlight+=("${#BUFFER} $(( ${#BUFFER} + ${#rest} )) ${style} memo=zss")
}

_zss_suggest() {
  (( ZSS_DISABLE )) && { _zss_clear_suggestion; return; }
  (( CURSOR != ${#BUFFER} )) && { _zss_clear_suggestion; return; }
  if [[ -z $BUFFER ]]; then
    _zss_clear_suggestion
    return
  fi
  if _zss_fetch "$BUFFER"; then
    ZSS_SUGGESTION=$REPLY
  else
    ZSS_SUGGESTION=""
  fi
  _zss_render
}

# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

_zss_widget_wrap() {
  # _zss_widget_wrap <widget-name> <new-function-name>
  # Saves whatever <widget-name> currently points to under
  # `_zss_orig_<widget-name>` (skipped for plain builtins/unset widgets,
  # since `_zss_call_orig` falls back to `zle .<widget-name>` for those),
  # then installs <new-function-name> as the widget.
  local orig=$1 new=$2
  local target=${widgets[$orig]:-}
  case $target in
    (user:*)
      zle -N _zss_orig_$orig ${target#user:}
      ;;
  esac
  zle -N $orig $new
}

_zss_call_orig() {
  local orig=$1; shift
  if (( ${+widgets[_zss_orig_$orig]} )); then
    zle _zss_orig_$orig -- "$@"
  else
    zle .$orig -- "$@" 2>/dev/null
  fi
}

_zss_self_insert() {
  _zss_call_orig self-insert
  _zss_suggest
}
_zss_widget_wrap self-insert _zss_self_insert

_zss_backward_delete_char() {
  _zss_call_orig backward-delete-char
  _zss_suggest
}
_zss_widget_wrap backward-delete-char _zss_backward_delete_char

if (( ${+widgets[bracketed-paste]} )); then
  _zss_bracketed_paste() {
    _zss_call_orig bracketed-paste
    _zss_suggest
  }
  _zss_widget_wrap bracketed-paste _zss_bracketed_paste
fi

_zss_history_nav() {
  local widget_name=$1
  _zss_call_orig $widget_name
  _zss_suggest
}
for _zss_w in up-line-or-history down-line-or-history up-line-or-search down-line-or-search history-search-backward history-search-forward; do
  (( ${+widgets[$_zss_w]} )) || continue
  eval "_zss_${_zss_w//-/_}() { _zss_history_nav $_zss_w }"
  _zss_widget_wrap $_zss_w _zss_${_zss_w//-/_}
done
unset _zss_w

_zss_accept_line() {
  _zss_clear_suggestion
  _zss_call_orig accept-line
}
_zss_widget_wrap accept-line _zss_accept_line

_zss_accept_or() {
  # _zss_accept_or <fallback-widget>
  # Accepts the current suggestion if there is one; otherwise runs
  # <fallback-widget> so the key isn't swallowed with no effect.
  # ZSS_SUGGESTION is always empty here when CURSOR isn't at the end of the
  # line (_zss_suggest clears it in that case), so this is also what makes
  # Right-arrow accept only at end-of-line and just move the cursor
  # otherwise, via the "forward-char" fallback its binding uses below.
  local fallback=$1
  if [[ -n $ZSS_SUGGESTION ]]; then
    BUFFER=$ZSS_SUGGESTION
    CURSOR=${#BUFFER}
    _zss_suggest
  else
    _zss_call_orig $fallback
  fi
}

zss-accept() {
  _zss_accept_or end-of-line
}
zle -N zss-accept

zss-accept-or-forward-char() {
  _zss_accept_or forward-char
}
zle -N zss-accept-or-forward-char

zss-accept-word() {
  if [[ -n $ZSS_SUGGESTION ]]; then
    local rest=${ZSS_SUGGESTION#$BUFFER}
    local -a words
    words=("${(z)rest}")
    if (( ${#words} > 0 )); then
      local w=${words[1]}
      # (ie): literal (non-pattern) index search. Without (e), `$w` is
      # matched as a GLOB pattern, so a suggestion word containing glob
      # characters (e.g. accepting one word of "echo a *.txt foo") could
      # match the wrong, shorter span of $rest instead of the literal word.
      local idx=${rest[(ie)$w]}
      BUFFER+=${rest[1,$(( ${#w} + (idx - 1) ))]}
    else
      BUFFER=$ZSS_SUGGESTION
    fi
    CURSOR=${#BUFFER}
    _zss_suggest
  else
    _zss_call_orig forward-word
  fi
}
zle -N zss-accept-word

zss-remove-current() {
  if [[ -z $ZSS_SUGGESTION ]]; then
    zle -M "zss: no suggestion to remove"
    return
  fi
  local victim=$ZSS_SUGGESTION
  # Deleting never blocks (that's manual-only, see _zss_is_blocked); this is
  # just an in-memory "don't re-suggest from the stale copy" note, see
  # _zss_is_locally_deleted's comment above.
  _zss_locally_deleted[$victim]=1
  _zss_clear_suggestion
  local out
  out=$(zss rm --exact -y -- "$victim" 2>&1)
  if (( $? == 0 )); then
    zle -M "zss: removed \"${victim//$'\n'/ }\""
  else
    zle -M "zss: failed to remove \"${victim//$'\n'/ }\": ${out//$'\n'/ }"
  fi
}
zle -N zss-remove-current

zss-toggle() {
  if (( ZSS_DISABLE )); then
    ZSS_DISABLE=0
    zle -M "zss: suggestions enabled"
  else
    ZSS_DISABLE=1
    _zss_clear_suggestion
    zle -M "zss: suggestions disabled"
  fi
}
zle -N zss-toggle

# ---------------------------------------------------------------------------
# zshaddhistory: keep blocked-prefix commands out of history; running a
# just-deleted command again lifts its local "don't re-suggest" note.
# ---------------------------------------------------------------------------

_zss_zshaddhistory() {
  local cmd=${1%$'\n'}
  unset "_zss_locally_deleted[$cmd]"
  _zss_is_blocked "$cmd" && return 1
  return 0
}
autoload -Uz add-zsh-hook 2>/dev/null
if (( ${+functions[add-zsh-hook]} )); then
  add-zsh-hook zshaddhistory _zss_zshaddhistory
  add-zsh-hook precmd _zss_refresh
else
  typeset -ga zshaddhistory_functions
  zshaddhistory_functions+=(_zss_zshaddhistory)
  typeset -ga precmd_functions
  precmd_functions+=(_zss_refresh)
fi

# ---------------------------------------------------------------------------
# Key bindings
# ---------------------------------------------------------------------------

bindkey '^[[C' zss-accept-or-forward-char   # Right arrow
bindkey '^[OC' zss-accept-or-forward-char   # Right arrow (application mode)
bindkey '^[[F' zss-accept   # End
bindkey '^[[4~' zss-accept  # End (some terminals)
bindkey '^[f' zss-accept-word     # Alt-f
bindkey '^[^[[C' zss-accept-word  # Alt-Right (common encoding)
bindkey '^X^D' zss-remove-current

# ---------------------------------------------------------------------------
# Highlighting (Phase 4)
# ---------------------------------------------------------------------------

[[ -f "$ZSS_PLUGIN_DIR/lib/highlight.zsh" ]] && source "$ZSS_PLUGIN_DIR/lib/highlight.zsh"

_zss_load_block_rules
_zss_last_block_mtime=0
_zss_last_hist_count=${#history}
zstat -H _zss_init_st -- "$(_zss_histfile)" 2>/dev/null && _zss_last_hist_mtime=${_zss_init_st[mtime]}

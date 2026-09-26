# zsh-smart-suggest built-in syntax highlighter.
#
# Runs from a zle-line-pre-redraw hook, owns only region_highlight entries
# tagged `memo=zss-hl` (Phase 3's ghost suggestion owns `memo=zss` and is
# never touched here), and never shells out or uses `whence` — all lookups
# go through zsh's own hash-table parameters ($commands, $aliases, ...).
#
# Region offsets everywhere in this file are 0-based with an EXCLUSIVE end
# (the same convention `region_highlight` itself uses), so a token's
# (start, end) pair can be appended to `region_highlight` unchanged.

zmodload zsh/parameter

: ${ZSS_HL_MAX_BUFFER:=2000}
: ${ZSS_HL_DISABLE:=0}

typeset -gA ZSS_HL_STYLES
typeset -g _zss_hl_last_buffer=""
typeset -ga _ZSS_HL_PRECOMMANDS
_ZSS_HL_PRECOMMANDS=(sudo command builtin exec noglob nocorrect env)

# ---------------------------------------------------------------------------
# Colour config: colors.conf is plain `key=style` data, parsed line by line;
# never sourced or eval'd, so a bad value is simply ignored, never executed.
# ---------------------------------------------------------------------------

_zss_hl_style_valid() {
  emulate -L zsh
  setopt extendedglob
  local names='black|red|green|yellow|blue|magenta|cyan|white|default'
  local hex='[0-9a-fA-F]'
  local color="(${names}|[0-9]##|\\#${hex}${hex}${hex}${hex}${hex}${hex})"
  local item="(fg=${color}|bg=${color}|bold|underline|standout)"
  [[ $1 == none ]] && return 0
  [[ -n $1 && $1 == ${~item}(,${~item})# ]] || return 1
  local part num
  for part in "${(s:,:)1}"; do
    if [[ $part == (fg|bg)=[0-9]## ]]; then
      num=${part#*=}
      (( num > 255 )) && return 1
    fi
  done
  return 0
}

_zss_hl_load_config() {
  emulate -L zsh
  setopt extendedglob
  ZSS_HL_STYLES=(
    command      "fg=green"
    alias        "fg=cyan"
    builtin      "fg=green,bold"
    function     "fg=blue"
    reserved     "fg=yellow"
    unknown      "fg=red,bold"
    option       "fg=magenta"
    string       "fg=yellow"
    variable     "fg=cyan"
    path         "underline"
    glob         "fg=blue"
    comment      "fg=8"
    operator     "fg=magenta,bold"
    redirect     "fg=magenta"
    assignment   "fg=blue"
    suggestion   "fg=8"
  )
  local f="${XDG_DATA_HOME:-$HOME/.local/share}/zsh-smart-suggest/colors.conf"
  [[ -f $f ]] || return
  local line key value
  local -a lines
  lines=("${(@f)$(<$f)}")
  for line in "${lines[@]}"; do
    [[ -z $line || $line == '#'* ]] && continue
    [[ $line == *'='* ]] || continue
    key=${line%%=*}
    value=${line#*=}
    (( ${+ZSS_HL_STYLES[$key]} )) || continue
    _zss_hl_style_valid "$value" && ZSS_HL_STYLES[$key]=$value
  done
}

_zss_hl_refresh_colors() {
  # Called from the plugin's precmd freshness check when colors.conf's
  # mtime changes, so open shells pick up new colours at their next prompt.
  _zss_hl_load_config
  _zss_hl_last_buffer=$'\0no-such-buffer\0'  # force a repaint next redraw
}

_zss_hl_load_config

# ---------------------------------------------------------------------------
# Tokeniser + classifier
# ---------------------------------------------------------------------------

_zss_hl_is_operator() {
  case $1 in
    (';'|'&&'|'||'|'|'|'|&'|'&'|'('|')'|'{'|'}'|'!') return 0 ;;
  esac
  return 1
}

_zss_hl_classify() {
  # _zss_hl_classify <word> <in-command-position:0|1>
  # Sets $REPLY to the class key (or "" for no highlight). Assumes the
  # caller (_zss_hl_scan) already set `emulate -L zsh; setopt extendedglob`
  # — no subshell/command-substitution here, which would fork per token and
  # is what made this show up in profiling at 50+ tokens/redraw.
  local w=$1
  integer cmdpos=$2

  if [[ $w == '#'* ]]; then
    REPLY=comment
    return
  fi
  if _zss_hl_is_operator "$w"; then
    REPLY=operator
    return
  fi
  if [[ $w == [0-9]#(\>\>|\>\&|\&\>\>|\&\>|\>|\<\<\<|\<\<|\<) ]]; then
    REPLY=redirect
    return
  fi
  if [[ $w == (\'*|\"*|\$\'*) ]]; then
    REPLY=string
    return
  fi

  if (( cmdpos )); then
    if [[ $w == [A-Za-z_][A-Za-z0-9_]#=* ]]; then
      REPLY=assignment
      return
    fi
    if [[ $w == -* ]]; then
      REPLY=""
      return
    fi
    if (( ${reswords[(Ie)$w]} )); then
      REPLY=reserved
      return
    fi
    if (( ${+aliases[$w]} )); then
      REPLY=alias
      return
    fi
    if (( ${+builtins[$w]} )); then
      REPLY=builtin
      return
    fi
    if (( ${+functions[$w]} )); then
      REPLY=function
      return
    fi
    if [[ $w == */* ]]; then
      # `~name/...` for a nonexistent user/named directory raises a fatal
      # "no such user or named directory" error on tilde expansion itself
      # (not a glob-match failure `nonomatch` would suppress), which would
      # abort this scan mid-redraw. Only expand the safe `~`/`~/...` forms;
      # skip the stat for any other tilde prefix instead of risking that.
      if [[ $w == \~* && $w != (\~|\~/*) ]]; then
        REPLY=unknown
      elif (( ${#w} <= 256 )) && [[ -x ${~w} ]]; then
        REPLY=command
      else
        REPLY=unknown
      fi
      return
    fi
    if (( ${+commands[$w]} )); then
      REPLY=command
      return
    fi
    REPLY=unknown
    return
  fi

  if [[ $w == -* ]]; then
    REPLY=option
    return
  fi
  if [[ $w == '$'* ]]; then
    REPLY=variable
    return
  fi
  if [[ $w == *[\*\?\[]* ]]; then
    REPLY=glob
    return
  fi
  # Only pay for a stat() when the word actually looks path-like (has a
  # slash or a leading ~): this is what keeps a long line of plain argument
  # words fast, since every one of them would otherwise hit the filesystem.
  if [[ $w == \~* && $w != (\~|\~/*) ]]; then
    # See the cmdpos branch above: `~othername` can raise a fatal "no such
    # user or named directory" error on expansion, so skip the stat rather
    # than risk aborting the highlight scan on every keystroke.
    REPLY=""
    return
  fi
  if [[ $w == (*/*|\~*) ]] && (( _zss_hl_path_checks < 20 )) && (( ${#w} <= 256 )); then
    _zss_hl_path_checks+=1
    if [[ -e ${~w} ]]; then
      REPLY=path
      return
    fi
  fi
  REPLY=""
}

_zss_hl_scan() {
  # _zss_hl_scan <buffer> <callback>
  # Calls `<callback> <start> <end> <key>` (0-based, end-exclusive) for
  # every classified token. On any offset mismatch, stops rather than risk
  # painting a wrong region.
  emulate -L zsh
  setopt extendedglob
  local buf=$1 cb=$2
  local -a words
  words=(${(Z+c+)buf})
  integer blen=${#buf}
  integer pos=1
  integer expect_cmd=1
  integer _zss_hl_path_checks=0
  local w key c
  integer wlen start end

  for w in "${words[@]}"; do
    wlen=${#w}
    (( wlen == 0 )) && continue

    while (( pos <= blen )); do
      c=${buf[pos]}
      if [[ $c == ' ' || $c == $'\t' || $c == $'\n' ]]; then
        (( pos++ )); continue
      fi
      if [[ $c == '\\' && ${buf[pos+1]} == $'\n' ]]; then
        (( pos += 2 )); continue
      fi
      break
    done

    start=$pos
    end=$(( pos + wlen - 1 ))
    if (( end > blen )) || [[ ${buf[start,end]} != $w ]]; then
      return
    fi

    _zss_hl_classify "$w" $expect_cmd
    key=$REPLY
    [[ -n $key ]] && "$cb" $(( start - 1 )) $end "$key"
    pos=$(( end + 1 ))

    case $w in
      (';'|'&&'|'||'|'|'|'|&'|'&'|'('|'{'|'!')
        expect_cmd=1
        continue
        ;;
      (if|then|else|elif|do|while|until|time)
        expect_cmd=1
        continue
        ;;
    esac
    if (( ${_ZSS_HL_PRECOMMANDS[(Ie)$w]} )); then
      expect_cmd=1
      continue
    fi
    if (( expect_cmd )); then
      if [[ $w == [A-Za-z_][A-Za-z0-9_]#=* ]]; then
        continue
      fi
      if [[ $w == -* ]]; then
        continue
      fi
      expect_cmd=0
    fi
  done
}

_zss_hl_tokens() {
  # Test/debug entry point: prints "<start> <end> <key>" per region.
  _zss_hl_scan "$1" _zss_hl_print_token
}

_zss_hl_print_token() {
  print -r -- "$1 $2 $3"
}

# ---------------------------------------------------------------------------
# region_highlight painting
# ---------------------------------------------------------------------------

_zss_hl_clear_region_highlight() {
  local -a kept
  local entry
  for entry in "${region_highlight[@]}"; do
    [[ $entry == *'memo=zss-hl' ]] && continue
    kept+=("$entry")
  done
  region_highlight=("${kept[@]}")
}

_zss_hl_add_region() {
  local style=${ZSS_HL_STYLES[$3]:-}
  [[ -z $style || $style == none ]] && return
  region_highlight+=("$1 $2 $style memo=zss-hl")
}

_zss_hl_redraw() {
  (( ZSS_HL_DISABLE )) && return
  (( PENDING > 0 )) && return
  [[ $BUFFER == $_zss_hl_last_buffer ]] && return
  _zss_hl_last_buffer=$BUFFER
  _zss_hl_clear_region_highlight
  (( ${#BUFFER} > ZSS_HL_MAX_BUFFER )) && return
  _zss_hl_scan "$BUFFER" _zss_hl_add_region
}

add-zle-hook-widget zle-line-pre-redraw _zss_hl_redraw

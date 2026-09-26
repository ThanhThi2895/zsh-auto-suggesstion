# Minimal zsh test harness: source this, call pass/fail/assert_eq, end with `summary`.
typeset -gi _zss_test_pass=0
typeset -gi _zss_test_fail=0

pass() { _zss_test_pass+=1; print -r -- "  ok   - $1"; }
fail() { _zss_test_fail+=1; print -r -- "  FAIL - $1"; }

assert_eq() {
  local got=$1 want=$2 msg=$3
  if [[ $got == $want ]]; then
    pass "$msg"
  else
    fail "$msg (got: '$got', want: '$want')"
  fi
}

assert_true() {
  local cond=$1 msg=$2
  if (( cond )); then
    pass "$msg"
  else
    fail "$msg"
  fi
}

summary() {
  print -r -- "-- ${_zss_test_pass} passed, ${_zss_test_fail} failed --"
  (( _zss_test_fail == 0 ))
}

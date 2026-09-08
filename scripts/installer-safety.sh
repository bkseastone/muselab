#!/usr/bin/env bash
# Source-only checks. No signals, service changes or configuration writes here.

muselab_pid_descends_from() {
  local child="$1" ancestor="$2" parent hops=0
  [[ "$child" =~ ^[0-9]+$ && "$ancestor" =~ ^[0-9]+$ ]] || return 1
  (( child > 1 && ancestor > 1 )) || return 1
  while (( hops < 64 )); do
    [[ "$child" == "$ancestor" ]] && return 0
    parent="$(ps -p "$child" -o ppid= 2>/dev/null | tr -d '[:space:]')"
    [[ "$parent" =~ ^[0-9]+$ ]] || return 1
    (( parent > 1 && parent != child )) || return 1
    child="$parent"
    hops=$((hops + 1))
  done
  return 1
}

muselab_port_is_owned() {
  local platform="$1" repo="$2" holders="$3" owner cwd holder
  [[ -n "$holders" ]] || return 1
  if [[ "$platform" == linux ]]; then
    owner="$(systemctl --user show muselab.service --property=MainPID --value 2>/dev/null)" || return 1
    cwd="$(systemctl --user show muselab.service --property=WorkingDirectory --value 2>/dev/null)" || return 1
  elif [[ "$platform" == macos ]]; then
    owner="$(launchctl list 2>/dev/null | awk '$3 == "com.muselab" {print $1}')"
    [[ "$owner" =~ ^[0-9]+$ ]] || return 1
    cwd="$(lsof -a -p "$owner" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  else
    return 1
  fi
  [[ "$owner" =~ ^[0-9]+$ && "${cwd%/}" == "${repo%/}" ]] || return 1
  for holder in $holders; do
    muselab_pid_descends_from "$holder" "$owner" || return 1
  done
}

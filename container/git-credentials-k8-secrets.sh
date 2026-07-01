#!/bin/sh
set -eu

CRED_PATH="${GIT_CREDENTIAL_PATH:-${GIT_CREDENTIAL_DIR:-/run/secrets/git-credentials}}"

protocol=""
host=""
input=""

while IFS='=' read -r key value; do
  input="${input}${key}=${value}
"
  case "$key" in
    protocol) protocol="$value" ;;
    host) host="$value" ;;
  esac
done

[ "$protocol" = "https" ] || exit 0
[ -n "$host" ] || exit 0

# 1. Prefer Kubernetes Secret-mounted files.
if [ -d "$CRED_PATH" ]; then
  username_file="${CRED_PATH}/${host}/username"
  password_file="${CRED_PATH}/${host}/password"

  if [ -r "$username_file" ] && [ -r "$password_file" ]; then
    printf 'username=%s\n' "$(cat "$username_file")"
    printf 'password=%s\n' "$(cat "$password_file")"
    exit 0
  fi
fi

# 2. Support the old single-file git credential-store layout.
if [ -f "$CRED_PATH" ] && [ -r "$CRED_PATH" ]; then
  stored="$(printf '%s\n' "$input" | git credential-store --file="$CRED_PATH" get || true)"
  if [ -n "$stored" ]; then
    printf '%s\n' "$stored"
    exit 0
  fi
fi

# 3. Fallback to environment variables.
# github.com -> GITHUB_COM
# git.example.com:8443 -> GIT_EXAMPLE_COM_8443
host_key="$(printf '%s' "$host" | tr '[:lower:]' '[:upper:]' | sed 's/[^A-Z0-9]/_/g')"

username_var="GIT_CREDENTIAL_${host_key}_USERNAME"
password_var="GIT_CREDENTIAL_${host_key}_PASSWORD"

# POSIX-safe indirect env lookup.
username="$(printenv "$username_var" || true)"
password="$(printenv "$password_var" || true)"

if [ -n "$username" ] && [ -n "$password" ]; then
  printf 'username=%s\n' "$username"
  printf 'password=%s\n' "$password"
  exit 0
fi

# No credentials found.
exit 0

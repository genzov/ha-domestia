#!/usr/bin/env bash
# Prints the GitHub release notes for a version: its CHANGELOG section, with
# wrapped lines joined (GitHub renders single newlines as line breaks), followed
# by a compare link to the previous tag. Fails if the CHANGELOG has no entry.
#
# Usage: release-notes.sh <version>
set -euo pipefail

version=${1:?usage: release-notes.sh <version>}
changelog="$(dirname "$0")/../../CHANGELOG.md"
repo_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-genzov/go-domestia}"

notes=$(awk -v heading="## $version" '
  function flush() {
    if (buf == "") return
    if (blank) print ""
    print buf
    printed = 1; blank = 0; buf = ""
  }
  $0 == heading { found = 1; print; printed = 1; blank = 1; next }
  !found { next }
  /^## / { exit }
  /^[[:space:]]*$/ { flush(); if (printed) blank = 1; next }
  /^- / { flush(); buf = $0; next }
  {
    line = $0
    sub(/^[[:space:]]+/, "", line)
    buf = (buf == "") ? line : buf " " line
  }
  END { flush() }
' "$changelog")

# Only the heading, or nothing at all, means there is no entry
if [ "$(grep -c . <<< "$notes")" -le 1 ]; then
  echo "No '## $version' section in CHANGELOG.md" >&2
  exit 1
fi

printf '%s\n' "$notes"

previous=$(git describe --tags --abbrev=0 2>/dev/null || true)
if [ -n "$previous" ]; then
  printf '\n**Full Changelog**: %s/compare/%s...%s\n' "$repo_url" "$previous" "$version"
fi

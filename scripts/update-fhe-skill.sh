#!/usr/bin/env bash
# Install the fhe-application-design skill from the niobium-skills catalog into
# both .claude/skills/ and .agents/skills/.
#
# The skill is installed on demand (via `make sync-skill`) rather than committed
# or mounted as a submodule: the catalog keeps the skill under skills/<name>/
# (a submodule can't mount a subdirectory) and symlinks are Windows-fragile. The
# installed copies are gitignored. This script writes both copies from the given
# ref and records the provenance in each copy's .vendored-from file.
#
# Usage:
#   scripts/update-fhe-skill.sh [<git-ref>]
#     <git-ref>  commit SHA / tag / branch in niobium-skills (default: main).
#                Pin a specific commit for a reproducible bump.
#
# Tool-agnostic on purpose: plain `git clone` + `tar`, no npx / network-quirk
# dependencies, so it runs the same locally and in restricted CI.
#
# Wired into `make sync-skill`, which runs this with the default ref.
set -euo pipefail

REPO_URL="https://github.com/NiobiumInc/niobium-skills"
SKILL_SUBDIR="skills/fhe-application-design"
SKILL_REF="${1:-main}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Shallow-clone the ref directly when it is a branch or tag; fall back to a
# full clone + checkout for a pinned commit SHA (which --branch cannot fetch).
if git clone --quiet --depth 1 --branch "$SKILL_REF" "$REPO_URL" "$TMP/niobium-skills" 2>/dev/null; then
  :
else
  git clone --quiet "$REPO_URL" "$TMP/niobium-skills"
  git -C "$TMP/niobium-skills" checkout --quiet "$SKILL_REF"
fi
RESOLVED="$(git -C "$TMP/niobium-skills" rev-parse HEAD)"

for dest in .claude/skills .agents/skills; do
  target="$ROOT/$dest/fhe-application-design"
  rm -rf "$target"
  mkdir -p "$target"
  git -C "$TMP/niobium-skills" archive HEAD "$SKILL_SUBDIR" \
    | tar -x --strip-components=2 -C "$target"
  printf 'NiobiumInc/niobium-skills@%s   # %s\n' "$RESOLVED" "$SKILL_SUBDIR" \
    > "$target/.vendored-from"
done

echo "Vendored $SKILL_SUBDIR @ $RESOLVED into .claude/skills/ and .agents/skills/"
echo "Review with: git status && git diff --stat"

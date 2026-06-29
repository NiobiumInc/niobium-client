#!/usr/bin/env bash
# Refresh the vendored fhe-application-design skill from the niobium-skills catalog.
#
# The skill is vendored (committed) into both .claude/skills/ and .agents/skills/
# rather than mounted as a submodule, because the catalog keeps the skill under
# skills/<name>/ (a submodule can't mount a subdirectory) and symlinks are
# Windows-fragile. This script re-vendors both copies from a pinned ref and
# records the provenance in each copy's .vendored-from file.
#
# Usage:
#   scripts/update-fhe-skill.sh [<git-ref>]
#     <git-ref>  commit SHA / tag / branch in niobium-skills (default: main).
#                Pin a specific commit for a reproducible bump.
#
# Tool-agnostic on purpose: plain `git clone` + `tar`, no npx / network-quirk
# dependencies, so it runs the same locally and in restricted CI.
#
# NOTE: the catalog layout (skills/<name>/) currently lives on the
# refactor/skill-directory-layout branch, not main, so until it merges pass an
# explicit ref:
#   scripts/update-fhe-skill.sh refactor/skill-directory-layout
set -euo pipefail

REPO_URL="https://github.com/NiobiumInc/niobium-skills"
SKILL_SUBDIR="skills/fhe-application-design"
SKILL_REF="${1:-main}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet "$REPO_URL" "$TMP/niobium-skills"
git -C "$TMP/niobium-skills" checkout --quiet "$SKILL_REF"
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

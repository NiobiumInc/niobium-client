# ==============================================================================
# Source tarball — self-contained release artifact (make/source-tarball.mk)
# ==============================================================================
# Produces niobium-client-<version>.tar.gz vendoring the submodule sources, for
# the from-source install targets for Homebrew / Linux / Docker (WIP).
# GitHub's auto-generated tarball leaves submodules EMPTY; this doesn't.
# Excludes niobium-haze (entry point 4, deferred — no target builds it yet).
#
# Assembles from `git archive` (tracked files only — no .git, no build trees)
# into a staging dir, then a plain `tar -c`; cross-platform (no GNU-tar-only
# --transform / --concatenate). Requires the submodules present: `make sync`.
# The release workflow calls `make source-tarball VERSION=...`, mirroring how
# runtime-release.yml calls `make runtime`.

# Version defaults to the repo-wide source of truth (the root VERSION file);
# override with VERSION=x.y.z. At release the workflow passes the git tag instead.
VERSION ?= $(shell cat VERSION 2>/dev/null)
SOURCE_TARBALL_OUTDIR ?= dist
SOURCE_TARBALL_PREFIX = niobium-client-$(VERSION)
SOURCE_TARBALL = $(SOURCE_TARBALL_OUTDIR)/$(SOURCE_TARBALL_PREFIX).tar.gz

.PHONY: source-tarball check-source-tarball

source-tarball: ## Build the self-contained source tarball (override VERSION=x.y.z)
	@set -e; \
	work=$$(mktemp -d); trap 'rm -rf "$$work"' EXIT; \
	stage="$$work/$(SOURCE_TARBALL_PREFIX)"; mkdir -p "$$stage"; \
	git archive HEAD | tar -x -C "$$stage"; \
	STAGE="$$stage" git submodule foreach --recursive --quiet ' \
		case "$$displaypath" in vendor/niobium-haze*) exit 0 ;; esac; \
		mkdir -p "$$STAGE/$$displaypath"; \
		git archive HEAD | tar -x -C "$$STAGE/$$displaypath" '; \
	c=$$(git rev-parse HEAD); \
	f=$$(git -C vendor/niobium-fhetch rev-parse HEAD 2>/dev/null || echo unknown); \
	o=$$(git -C vendor/niobium-fhetch/vendor/openfhe rev-parse HEAD 2>/dev/null || echo unknown); \
	printf '{\n  "name": "niobium-client",\n  "version": "%s",\n  "commit": "%s",\n  "submodules": {\n    "niobium-fhetch": "%s",\n    "openfhe": "%s"\n  }\n}\n' \
		"$(VERSION)" "$$c" "$$f" "$$o" > "$$stage/manifest.json"; \
	find "$$stage" -type d -empty -delete; \
	mkdir -p $(SOURCE_TARBALL_OUTDIR); \
	tar -czf $(SOURCE_TARBALL) -C "$$work" "$(SOURCE_TARBALL_PREFIX)"; \
	{ command -v sha256sum >/dev/null 2>&1 && sha256sum $(SOURCE_TARBALL) || shasum -a 256 $(SOURCE_TARBALL); }

check-source-tarball: ## Validate the built source tarball is self-contained
	@set -e; \
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	tar -xzf $(SOURCE_TARBALL) -C "$$tmp"; \
	root="$$tmp/$(SOURCE_TARBALL_PREFIX)"; \
	fail() { echo "FAIL: $$1" >&2; exit 1; }; \
	[ -d "$$root" ] || fail "missing top dir $(SOURCE_TARBALL_PREFIX)/"; \
	[ -f "$$root/CMakeLists.txt" ] || fail "missing CMakeLists.txt"; \
	[ -f "$$root/VERSION" ] || fail "VERSION file missing (CMake reads it)"; \
	[ -f "$$root/vendor/niobium-fhetch/CMakeLists.txt" ] || fail "niobium-fhetch not vendored"; \
	[ -f "$$root/vendor/niobium-fhetch/vendor/openfhe/CMakeLists.txt" ] || fail "instrumented OpenFHE not vendored"; \
	[ -d "$$root/dsl_fhe/xcomp" ] || fail "nb DSL (xcomp) missing"; \
	[ -f "$$root/manifest.json" ] || fail "provenance manifest missing"; \
	grep -q "\"version\": \"$(VERSION)\"" "$$root/manifest.json" || fail "manifest version != $(VERSION)"; \
	if find "$$root" -name .git -print -quit | grep -q .; then fail ".git present in tarball"; fi; \
	if find "$$root" \( -name build -o -name dbuild \) -type d -print -quit | grep -q .; then fail "a build/ or dbuild/ tree is present"; fi; \
	if [ -e "$$root/vendor/niobium-haze" ]; then fail "niobium-haze should be excluded"; fi; \
	echo "OK: $(SOURCE_TARBALL_PREFIX) is self-contained ($$(du -sh "$$root" | cut -f1) unpacked)"

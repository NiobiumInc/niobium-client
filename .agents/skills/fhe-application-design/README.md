# FHE Application Design: an AI Agent Skill for FHE

An AI Agent Skill that guides developers through designing and building Fully Homomorphic Encryption (FHE) applications with the Niobium DSL and OpenFHE. It provides an 8-stage methodology covering the full design process, from establishing a privacy model through implementation and protocol specification.

## What This Skill Does

When a user asks the agent to design an FHE application, this skill provides structured guidance through eight stages:

1. **Privacy model** — identify parties, adversaries, encryption model (single vs. independent encryptors), output privacy, and output integrity (transciphering)
2. **Feasibility assessment** — data-obliviousness, arithmetic lane, multiplicative depth, SIMD parallelism
3. **Plaintext algorithm** — get it working unencrypted first, with client-server separation
4. **Scheme selection** — CKKS vs. BFV vs. BGV, with justification
5. **Circuit design** — SIMD packing, comparison strategies, depth budget, transciphering layer
6. **Parameter selection** — ring dimension, depth, scaling modulus, size/bandwidth estimation
7. **Implementation** — two tracks: the `nb` FHE DSL (niobium-client; generates the whole pipeline from ~3 short files) or raw OpenFHE C++ (four-program architecture: keygen, encrypt, server, decrypt, plus test runner)
8. **Protocol specification** — message flow, threat model, information leakage, integrity guarantees

The skill ensures that common protocol-level errors are caught early: packing data across privacy boundaries, ignoring output integrity when the decryptor differs from the consumer, conflating SIMD parallelism with task-level concurrency, and over-provisioning ciphertext depth.

## Prerequisites

The skill assumes the user:

- Is comfortable writing C++ or Python
- Has a basic understanding of what encryption does (not necessarily FHE-specific knowledge)
- Has access to OpenFHE (the skill references OpenFHE APIs throughout)

The skill does *not* assume prior FHE experience. It is designed to take a developer from "I have a computation I want to run on encrypted data" to a working implementation.

## Installation

This skill ships in the [`niobium-skills`](https://github.com/NiobiumInc/niobium-skills)
catalog as an [Agent Skill](https://agentskills.io/specification) — a portable `SKILL.md`
plus `references/`. It works with any agent that supports the standard.

### Recommended: universal installer

```bash
npx skills add NiobiumInc/niobium-skills --skill fhe-application-design
```

This uses [skills.sh](https://skills.sh), a third-party cross-agent installer. It
auto-detects your agent and writes the full skill folder (including `references/`) to the
right directory — supporting Claude Code, Cursor, Codex, GitHub Copilot, Windsurf, Gemini,
Cline, and ~20 more.

### GitHub CLI

```bash
gh skill install NiobiumInc/niobium-skills fhe-application-design
```

### Fallback: manual install

Clone the catalog and copy this skill's folder (preserving `references/`) into the location
for your agent:

```bash
git clone https://github.com/NiobiumInc/niobium-skills
cp -r niobium-skills/skills/fhe-application-design ~/.claude/skills/fhe-application-design
```

| Agent | Per-project | Per-user |
| --- | --- | --- |
| Claude family | `.claude/skills/fhe-application-design/` | `~/.claude/skills/fhe-application-design/` |
| Codex CLI | `.agents/skills/fhe-application-design/` | `~/.agents/skills/fhe-application-design/` |
| Other agentskills.io agents (Cursor, Copilot, Windsurf, Gemini, Cline) | `.agents/skills/fhe-application-design/` | `~/.agents/skills/fhe-application-design/` |

Notes:

- The installed directory name must match the `name` in `SKILL.md` frontmatter (`fhe-application-design`).
- Preserve the folder structure — `SKILL.md` references files in `references/` by relative path.
- Your agent may need a restart to detect the newly installed skill.
- Codex scans `.agents/skills` from the current working directory up to the repo root.
- For any agent not listed here, use the `npx skills add` installer above or see
  [agentskills.io](https://agentskills.io) — don't assume an install path.

## Directory Structure

Within the `niobium-skills` catalog, this skill is self-contained at
`skills/fhe-application-design/`:

```
skills/fhe-application-design/
├── SKILL.md                  # Main skill instructions (read by the agent)
├── README.md                 # This file (for human readers)
├── LICENSE                   # Apache-2.0 (bundled so an installed skill is self-contained)
├── references/               # Domain knowledge files read on demand
│   ├── fhe-privacy-model.md
│   ├── fhe-what-fhe-can-and-cannot-do.md
│   ├── fhe-scheme-selection.md
│   ├── building-your-first-fhe-application.md
│   ├── fhe-application-dialogue.md
│   ├── example-set-membership.md
│   ├── example-fetch-by-similarity.md
│   ├── example-network-intrusion-detection.md
│   ├── openfhe-examples-catalog.md
│   └── implementing-with-nb-dsl.md
└── evals/                    # Evaluation suite for skill quality
    └── evals.json
```

The `references/` directory contains ~1,400 lines of domain knowledge that the agent reads selectively during each stage. These include worked examples (set membership search, similarity fetch, network intrusion detection), scheme selection guidance, and an OpenFHE API catalog.

The `evals/` directory contains three test prompts (credit scoring, credential search, salary statistics) with assertions for regression testing. These are development artifacts — not needed by end users, but useful for anyone modifying SKILL.md.

## Version History

**Iteration 4 (current)** — Eval results: 100% pass rate (36/36 assertions) across 3 eval scenarios; baseline without skill passes 88.9% (34/36).

Changes across iterations:

- **Iteration 1:** Initial 8-stage methodology with scheme selection, circuit design, parameter selection, and implementation guidance.
- **Iteration 2:** Added Stage 1 Question 2 (single vs. independent encryptors) and Stage 5 packing-privacy cross-check. Fixes a flaw where the skill recommended column-major SIMD packing without verifying the encryption model supports it.
- **Iteration 3:** Added output integrity via transciphering (Stage 1 Question 5, Stage 5 transciphering section, Stage 8 protocol item). Addresses the protocol flaw where a decryptor can misrepresent results to the consumer.
- **Iteration 4:** Refined to dual-output transciphering pattern (decryptor sees result AND passes opaque symmetric ciphertext to consumer). Added honest SIMD assessment distinguishing task-level concurrency from SIMD parallelism. Added ciphertext/key size estimation formulas to Stage 6. Restructured Stage 7 around four separate programs (keygen, encrypt, server, decrypt) plus a test runner.

## Maintainer Notes

If you modify SKILL.md, re-run the eval suite to verify quality. The three eval prompts exercise different FHE design patterns:

- **Credit scoring** (independent encryptors, output integrity problem, logistic regression circuit) — tests transciphering, packing-privacy consistency, SIMD honesty
- **Credential search** (single encryptor for database, no integrity problem, set membership) — tests column-major packing, scale handling, comparison circuit design
- **Salary statistics** (multi-party, threshold/shared key considerations, aggregation) — tests multi-party awareness, packing-privacy with shared keys, low-depth circuit

The discriminating assertions — the ones the baseline fails but the skill passes — are: `output-integrity-addressed`, `packing-privacy-consistent`, `simd-honest-assessment`, and `depth-analysis` (when transciphering is relevant). These represent real protocol-level flaws that would produce insecure or broken deployments.

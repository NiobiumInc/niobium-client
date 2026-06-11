# Fraud-Flag Checking — Private Set Membership for Card Numbers

A client holds a private 16-digit card number; a server holds a private list
of ~5,000 flagged card numbers. The client learns whether their card is
flagged — without revealing the number to the server, and without the server
revealing anything else about its list.

This example was produced by following the **fhe-application-design skill**
end to end (Stages 1–8) and implementing via **Stage 7 Track A** (the nb
DSL). The full design rationale is recorded here as the Stage 8 protocol
specification.

## Stage 1 — Privacy model

| Question | Answer |
|---|---|
| Parties | Client (cardholder, holds the PAN); server (holds the flagged list) |
| Adversary | Semi-honest server: must not learn the queried PAN. Client must not learn the list beyond the one flag bit |
| Who encrypts | **Single encryptor** — the client encrypts only its own query. The server's list never leaves the server and stays plaintext there (folded into the circuit), so no cross-owner packing question arises |
| Who decrypts | The client — and the client is also the consumer of the result, so there is **no output-integrity problem** (no transciphering needed) |
| Output privacy | One bit per query. Repeated adaptive queries could probe the list; deploy behind query rate limiting |

## Stage 2 — Feasibility

Data-oblivious (a fixed arithmetic circuit touches every list entry every
time — which is also what hides the list), purely arithmetic lane, shallow
multiplication chain (2 + K), and natural SIMD parallelism (one flagged card
per slot). A clean fit.

## Stage 3 — Plaintext ground truth

`harness/encode_cards.py` is the plaintext implementation and test-data
generator: a deterministic (seeded) flagged list of 5,000 cards, digit
encoding, and the cleartext expected match count that `decrypt_verify`
asserts against.

## Stage 4 — Scheme

CKKS: the comparison is approximate by design (squared distance + iterated
squaring with a 0.5 decision threshold), so approximate arithmetic costs
nothing and buys the fastest bulk numeric throughput.

## Stage 5 — Circuit

- **Encoding**: each digit `d → d+1` (values 1..10), so the zero rows that
  pad the final batch can never match a real query.
- **Packing**: column-major — one ciphertext per digit position (L = 16),
  one flagged card per SIMD slot; 5,000 cards → 5 batches at 1,024 slots
  (Toy) or 1 batch at 16,384 slots (Full).
- **Comparison**: per slot, `S = Σ (q_i − c_i)²`; normalize `t = 1 − S/C`
  with `C = 9²·16 = 1296`; amplify with K iterated squarings; `slot_sum`
  aggregates. A flagged match contributes ≈ 1, everything else decays to ≈ 0.
- **K**: `(1 − 1/C)^(2^K) < 0.01` ⇒ `2^K > 1296·ln 100 ≈ 5969` ⇒ **K = 13**,
  total depth **2 + 13 = 15**.

## Stage 6 — Parameters

`logQ ≈ first_mod + depth × q_i = 60 + 15×50 = 810 bits` → 128-classic needs
only **ring_dim 32768**. The deployment, however, pins **ring_dim = 65536**
as a hard constraint (the hardware ring-dimension target) — above the
security minimum, which is pure upside: 32,768 slots pack all 5,000 flagged
cards in one batch, and the spare modulus budget is headroom the compiler
quantifies at `nbc check` time:

```
note: params: logQ ~= 810 bits (first_mod 60 + depth 15 x q_i 50);
128-classic needs ring_dim >= 32768; declared ring_dims OK: [65536];
headroom at N=65536: 962 bits -> q_i up to 59 (+9 bits/level precision)
or depth up to 34 (+19 levels);
below target: [2048] (covered by scheme.override(security: not_set) dev profiles)
```

The headroom line is the deployment-constrained tuning surface: raise `q_i`
toward the limb cap for tighter precision, or spend levels on higher
approximation degrees — without touching security.

## Stage 7 — Implementation (Track A)

Three `.nb` files (`shared.nb`, `client.nb`, `server.nb`) plus the harness.
The compiler generates the four-program pipeline, all serialization, matched
keygen, the Niobium record/replay instrumentation, and **cleartext reference
twins** (`*_ref` binaries) of every stage.

```bash
cd dsl_fhe && make test-fraud
# flagged card  -> score 1.000 (expect 1)
# clean card    -> score ~ -2e-13 (expect 0; the reference twin computes
#                  3e-81 — the difference between them IS the CKKS noise)
# reference pipeline re-verified as generated ground truth
```

## Stage 8 — Protocol and threat model

1. **Setup (once)**: client runs `key_generation`, sends `cc/pk/mk/rk` to
   the server. The secret key never leaves the client.
2. **Per query**: client runs `encrypt_card` (16 ciphertexts) → server runs
   `check_card` (no secret key linked; the flagged list read from local
   disk) → client runs `decrypt_verify` and thresholds at 0.5.
3. **What each party learns**: server sees only ciphertexts and learns
   nothing about the PAN (semantic security). Client learns one bit
   (plus the approximate match count if multiple entries collided — card
   numbers are unique, so this is exactly the flag).
4. **Incidental leakage**: the batch count reveals the list size to within
   `n_slots`; computation time is data-independent (oblivious circuit).
5. **Not addressed**: malicious server (could compute a different function),
   adaptive query enumeration (rate-limit upstream), traffic analysis.

## Files

| File | Role |
|---|---|
| `shared.nb` | Instance profiles, directory layout, wire types |
| `client.nb` | scheme + keygen, per-digit encryption, decrypt + verify |
| `server.nb` | `@hardware` squared-distance + iterated-squaring circuit |
| `harness/encode_cards.py` | Plaintext ground truth: list generation, encoding, expected count |

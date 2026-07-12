# Python examples (pip-installed `niobium_sdk`)

Python ports of the C++ examples in [`examples/`](../../examples/), using the installed
`niobium_sdk` wheel instead of a source build. Same **client → server →
decrypt** split as the C++ versions:

- **`client.py`** — generate a CKKS context + keys, encrypt inputs, serialize
  everything to a directory (pure OpenFHE, no Niobium session).
- **`server.py`** — deserialize, tag inputs/keys, record the computation as a
  FHETCH trace (`niobium_sdk.session`), replay it locally through the bundled
  `fhetch_sim`, and serialize the result ciphertext.
- **`decrypt.py`** — deserialize the secret key + result, decrypt, verify.

## Setup

```bash
pip install niobium_sdk        # or a local wheel: pip install ./niobium_sdk-*.whl
```

Everything the examples need (crypto, session/replay, the simulator) ships in the
wheel — no `LD_LIBRARY_PATH`/`DYLD_LIBRARY_PATH` or external OpenFHE required.

## Running

Each scenario writes its artifacts to a directory you pass (created if missing):

```bash
# mult — CKKS a * b
python mult/client.py   out            # defaults: a=7, b=13
python mult/server.py   out
python mult/decrypt.py  out            # -> PASS: 91.0

# simple_ops — pick an operation (ADD SUB MUL NEG ADDI ... MORPH)
python simple_ops/client.py  out 5 6
python simple_ops/server.py  out MUL
python simple_ops/decrypt.py out MUL   # -> PASS: 30.0

# plaintext_add — EvalAdd(ciphertext, server-side plaintext)
python plaintext_add/client.py  out
python plaintext_add/server.py  out
python plaintext_add/decrypt.py out

# bootstrap — CKKS EvalBootstrap
python bootstrap/client.py  out
python bootstrap/server.py  out
python bootstrap/decrypt.py out
```

The public surfaces used: `niobium_sdk.openfhe` (crypto) and
`niobium_sdk.session` (record/replay). To send a trace to a compilation
target instead of replaying locally, see `niobium_sdk.client.submit()`.

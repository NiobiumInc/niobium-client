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

The clients default to ring dimension **2^16 (65536)**, the only one Niobium
hardware runs, so these commands produce Fog-ready keys; bootstrap keygen at
2^16 takes minutes. For a quick local run, pass a smaller ring dimension as the
client's last argument and `--no-ring-dim-check` to the server, which is what the
`make test-*-python-release` targets do:

```bash
python mult/client.py  out 7 13 2048
python mult/server.py  out --no-ring-dim-check
```

2^11 with `HEStd_NotSet` is a test setting with no security, and the Fog refuses
`--no-ring-dim-check`; see "Ring dimension" in the top-level README.

The public surfaces used: `niobium_sdk.openfhe` (crypto) and
`niobium_sdk.session` (record/replay). To send a trace to a compilation
target instead of replaying locally, see `niobium_sdk.client.submit()`.

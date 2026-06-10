# fhe-NetworkMonitor assets

This example runs encrypted KitNET anomaly detection. It needs two data files at
runtime that are **not vendored** in this open-source client:

- `models/Mirai_model_<PROFILE>.bin` — the trained KitNET ensemble (autoencoder
  weights + anomaly detector), loaded by `load_kitnet_model()`.
- `datasets/Mirai_first_batch_32K.bin` — the input traffic feature matrix
  (`n_slots` packets × `n_features` doubles), loaded by `read2vecs<double>()`.

## Build & run with stubs (default, self-contained)

`make fhe-network-monitor` generates **stub** assets (zeros, minimal valid
KitNET dims) into `models/` and `datasets/` via `make_stub_assets.py`, so the
pipeline builds and runs end-to-end (keygen → encrypt → encrypted inference)
with no external repo. The anomaly scores are **not** meaningful. Generate them
manually for another profile with:

```bash
python3 examples/fhe-NetworkMonitor/assets/make_stub_assets.py TOY   # or MINI / FULL
```

The binary layouts match what the generated loaders expect (see
`nb_out/nb_shared.{h,cpp}`): the model is a 7×uint16 header, Chebyshev coeff
blocks (unused by the DSL, which recomputes them), a feature map, and the
autoencoder/detector weight matrices; the dataset is row-major float64.

## Run real detection (opt-in)

Obtain the trained model + Mirai dataset from the fhe-NetworkMonitor submission
package and build against them:

```bash
make fhe-network-monitor SUBMISSION_REPO=/path/to/submission-repo
```

When `SUBMISSION_REPO` is set, the Makefile populates `assets/` from that
checkout's `examples/fhe-NetworkMonitor/assets` instead of the stubs.

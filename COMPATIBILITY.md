# Compatibility and Versioning

This document describes how `niobium-client` and `niobium-fhetch` are versioned, and how
to tell whether a given client works with the Niobium compiler you are sending work to.

## Three version streams

| Component | What its version names |
| --- | --- |
| `niobium-client` | The client library and CLI you build and link against your application. |
| `niobium-fhetch` | The FHETCH IR implementation and its shared library. |
| Niobium compiler | The compiler and runtime that receive and execute your recorded program. |

**These are independent.** They do not move in lockstep, their major versions need not
match, and equal numbers across two of them mean nothing. Do not infer compatibility by
comparing them to each other — compatibility is declared explicitly, as described below.

## How compatibility is declared

The client is the *producer*: it links into your application, records the computation, and
emits a project bundle. The compiler is the *consumer*: it receives that bundle, compiles
it, and runs it.

So the question is never "can my client read the compiler's output?" but **"can the
compiler still accept what my client produced?"**

Every bundle the client emits carries the versions that produced it:

```json
"producer": {
  "client_version": "1.2.0+g1d7db00",
  "fhetch_version": "1.0.0"
}
```

The compiler declares the oldest client whose bundles it accepts, and checks it. If your
client is too old, the run is refused with a message naming the version to upgrade to:

```
this bundle was produced by niobium-client 1.2.0, which the server no longer
supports. Upgrade niobium-client to 1.4.0 or newer:
https://github.com/NiobiumInc/niobium-client
```

Two things follow that are worth knowing:

- **Only a minimum is enforced.** A client *newer* than the compiler is always accepted.
- **The minimum moves rarely** — only when support for older bundles is actually dropped,
  not on every release. Most client releases do not change it.

Building from source at an untagged commit is supported. Such a build reports its version
with a build suffix, for example `1.4.0+g1d7db00`, and is checked on the `1.4.0` part.

## When each component's version changes

### niobium-client

| Level | When |
| --- | --- |
| **patch** | Bug fixes. Performance improvements. No change to what it emits or how it communicates. |
| **minor** | New capability, backward compatible. The compiler still accepts bundles from older clients. |
| **major** | It emits something older compilers cannot consume, or compilers are dropping support for what older clients emit. |

Note performance work is a **patch**, not a minor bump: a faster client that emits
identical bundles has not changed its interface.

### niobium-fhetch

This library has two distinct consumer surfaces — a link-time API and a wire format — and
only changes to those propagate to anyone else. Most changes here affect nothing downstream.

| Change | Effect |
| --- | --- |
| Simulator internals, bug fixes | fhetch **patch**. Nothing else changes. |
| Additive API, new metadata tags | fhetch **minor**. Rebuild; no break. |
| `SOVERSION` change (link-time break) | fhetch **major** and client **major**. |
| Instruction renumbering or a binary-format version bump | fhetch **major**, client **major**, and a new compiler minimum. |

Note that the FHETCH binary format's metadata is a tag-length-value structure whose readers
skip tags they do not recognize, so new metadata fields can be added **without** a format
version bump. That is the preferred route.

## New hardware does not require a client upgrade

Running on newer Niobium hardware **never** requires you to upgrade the client. The client
holds no device model: it names a target as an opaque string, which the service resolves to
whatever hardware is currently promoted for it. Instruction encodings do not vary by
hardware generation, and the bundle you produce carries no device identity — selecting and
configuring hardware happens entirely on the service side.

The one change that can reach you is a new FHE operation you must call by name. That is a
change to the FHETCH API, and it follows the `niobium-fhetch` rules above; hardware that
merely runs existing operations faster never qualifies.

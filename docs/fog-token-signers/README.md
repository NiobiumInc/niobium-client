# Signing Fog V2 access tokens in an app backend

An end user of your application never has a Fog account. Your backend signs a
short-lived **access token** for them with a signing key your organization
registered (`fog keys create`, or `fog keys register` for a key made in the
backend), and the end user's application presents it to the Fog
(`POST /v2/jobs`). No call to the Fog is involved in signing.

Two reference signers, each about thirty lines, for the two backends we see
most: [`sign_token.py`](sign_token.py) (PyJWT) and [`sign_token.mjs`](sign_token.mjs)
(jose). Both produce exactly what `fog token sign` does.

The private key is PKCS#8 PEM (what `fog keys create` writes). The claims, and
the limits the registered key imposes on them, are in
`service-delivery-fogteam/FOG_V2_API.md` §1:

| Claim | Value |
|---|---|
| `iss` | your organization's ticker (printed by `fog keys create`, `organization` in `fog keys list`) |
| `aud` | the deployment's audience: `fog` |
| `sub` | the identity: your own opaque id for the end user, never personal data |
| `jti` | unique per token |
| `iat`, `exp` | at most the key's `max_lifetime_seconds` apart (15 minutes by default) |
| `grants` | `run` (versions, `max_jobs`), `jobs` (read, logs, cancel), `keys` (versions) |

Sign one token per run with `max_jobs: 1` unless you have a reason not to:
a token leaked from a device is then worth one job.

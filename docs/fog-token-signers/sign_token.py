"""Sign a Fog V2 access token for one end user.  pip install 'pyjwt[crypto]'"""
import time
import uuid

import jwt


def sign_access_token(private_key_pem: str, kid: str, organization: str, identity: str, *,
                      versions: list[str], max_jobs: int = 1, ttl: int = 900, audience: str = "fog") -> str:
    """A token that lets `identity` create `max_jobs` jobs under one of `versions`
    and read, tail and cancel its own jobs, for `ttl` seconds."""
    now = int(time.time())
    claims = {
        "iss": organization, "aud": audience, "sub": identity, "jti": str(uuid.uuid4()),
        "iat": now, "exp": now + ttl,
        "grants": {"run": {"versions": versions, "max_jobs": max_jobs},
                   "jobs": {"read": True, "logs": True, "cancel": True}},
    }
    return jwt.encode(claims, private_key_pem, algorithm="ES256", headers={"kid": kid})


if __name__ == "__main__":
    import os
    import sys
    # sign_token.py KID ORGANIZATION IDENTITY VERSION  (key from $FOG_SIGNING_KEY_PEM, a file path)
    kid, organization, identity, version = sys.argv[1:5]
    with open(os.environ["FOG_SIGNING_KEY_PEM"]) as f:
        print(sign_access_token(f.read(), kid, organization, identity, versions=[version]))

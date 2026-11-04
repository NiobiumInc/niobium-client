// Sign a Fog V2 access token for one end user.  npm install jose
import { randomUUID } from "node:crypto";
import { importPKCS8, SignJWT } from "jose";

/** A token that lets `identity` create `maxJobs` jobs under one of `versions`
 *  and read, tail and cancel its own jobs, for `ttl` seconds. */
export async function signAccessToken(privateKeyPem, kid, organization, identity,
                                      { versions, maxJobs = 1, ttl = 900, audience = "fog" }) {
  const key = await importPKCS8(privateKeyPem, "ES256");
  return new SignJWT({
      grants: { run: { versions, max_jobs: maxJobs }, jobs: { read: true, logs: true, cancel: true } },
    })
    .setProtectedHeader({ alg: "ES256", kid })
    .setIssuer(organization)
    .setAudience(audience)
    .setSubject(identity)
    .setJti(randomUUID())
    .setIssuedAt()
    .setExpirationTime(`${ttl}s`)
    .sign(key);
}

// node sign_token.mjs KID ORGANIZATION IDENTITY VERSION  (key from $FOG_SIGNING_KEY_PEM, a file path)
if (process.argv[1] === new URL(import.meta.url).pathname) {
  const { readFileSync } = await import("node:fs");
  const [kid, organization, identity, version] = process.argv.slice(2);
  console.log(await signAccessToken(readFileSync(process.env.FOG_SIGNING_KEY_PEM, "utf8"),
                                    kid, organization, identity, { versions: [version] }));
}

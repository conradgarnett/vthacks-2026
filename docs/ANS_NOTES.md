# ANS research notes

Bounded research (3 fetches, 2026-09-18). Everything below is what those pages said; details the
pages did not state are marked **not documented** and were **not** invented. SENSE implements an
ANS-_modeled_ identity layer, not ANS.

## Sources actually fetched

1. `https://github.com/godaddy/ans-registry` (registry README)
2. `https://datatracker.ietf.org/doc/draft-narajala-courtney-ansv2/` (IETF Internet-Draft page)
3. `https://ansinfo.ai` (project site)

## What the sources say

- **Domain-anchored identity.** Every agent's identity is tied to a domain name whose ownership has
  been verified. The draft's naming form is `ans://v{version}.{agentHost}`; "the domain is the
  agent's permanent address; the version number changes, the domain does not."
- **Dual certificates.** The draft describes a _Server Certificate_ from a public CA (ordinary TLS
  for the stable FQDN) and an _Identity Certificate_ from a private CA whose URI SAN carries the
  versioned ANS name. The registry README mentions a "version-bound certificate that records which
  code is running" but does not detail the split (not documented there).
- **Proof of control.** A Registration Authority verifies domain ownership via ACME (DNS-01 or
  HTTP-01) before attesting to an identity.
- **Transparency log.** An append-only log seals lifecycle events; the draft says it produces
  inclusion and consistency proofs and exposes an unauthenticated REST API. The README references
  SCITT statements/receipts, checkpoints and tiles, and Merkle-tree infrastructure without
  implementation detail.
- **Version immutability.** Entries "cannot be altered after the fact" via sealed lifecycle events;
  the mechanism is not explained on the README page.
- **Relationship to MCP / A2A.** The draft says MCP and A2A define how agents communicate but
  defer who the agent is and whether to trust it; ANS fills that gap through an SDK with protocol
  adapters. Concrete integration workflows are **not documented** in the draft page.
- **APIs.** The README links an OpenAPI specification and SDKs (Rust, Go, Java); payload schemas
  were **not** available from the fetched pages.
- **Governance (differs from the brief).** ansinfo.ai describes ANS as a GoDaddy-led open-source
  project with ecosystem collaboration. It mentions the Linux Foundation as the home of the A2A/MCP
  protocol layer, **not** as ANS's governance body. SENSE docs therefore avoid claiming Linux
  Foundation governance for ANS.

## How this shaped the design

| Source idea                            | SENSE implementation (`packages/identity`)                                                           |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| FQDN-anchored identity                 | Every check binds to the FQDN in the record, the certificates and the signed Sense Card.             |
| Server cert + per-version identity cert | `LocalCA` has two roots: a "server" CA (FQDN-bound cert) and an "identity" CA (per-version cert).   |
| Versioned name `ans://v{ver}.{host}`   | Identity cert URI SAN uses exactly that form; a private-arc extension carries the code digest.       |
| Version-bound / immutable              | Registered digest is compared to the digest the live agent presents (step 5).                        |
| Append-only transparency log           | RFC 6962-style Merkle tree with real inclusion proofs and signed tree heads (step 6).                |
| Proof of control (ACME)                | **Not modeled.** The simulated registry trusts the sim's own registration calls; noted as a gap.     |
| Consistency proofs                     | **Simplified.** Clients pin the last tree head and check `rootAt(size)` matches (roadmap: RFC proofs).|

## What is not real

- No ACME, no real DNS/DNSSEC, no public CA. The "domains" are simulated `.sim` names.
- The custom certificate extension OID (`1.3.6.1.4.1.99999.1.1`) is a placeholder in a private arc,
  not an assigned OID and not ANS's.
- `LiveAnsClient` (`ANS_MODE=live`) is a typed stub that throws `NotConfigured`. The fetched pages
  did not give enough wire-format detail to implement it honestly, and no recorded fixtures exist.

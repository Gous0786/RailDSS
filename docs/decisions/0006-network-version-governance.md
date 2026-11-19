# DR-0006: Version governance for the published network

| Field | Value |
|---|---|
| Spec references | FR-DATA-03, FR-DATA-04; DR-0002 F06; DR-0003 (consequences) |
| Issues | #63 (this change); #62 (DQ gate decoupled from the register hash) |
| Date | 2026-11-19 |
| Status | **Accepted** (PR merged) |
| Supersedes | n/a |

## Context

`raildss network publish` writes `data/published/<network_version>/`: one JSON file per canonical
entity, plus `network.json`, a provenance card and a manifest. The network hash was a SHA-256 over
**every** entity file, including `network_version.json`. That record carries metadata about the
publication: the validator report, the DQ report and the SHA-256 of every file under the source
directories.

Two consequences followed:

- **Metadata changed the hash.** PR #50 edited the parameter register and changed the hash of
  `KTV-PSA-2025-08`, with no change to the network (DR-0002 F06, DR-0003). The same happened
  whenever a twin calibration output was regenerated, because those outputs sit next to the
  derived inputs and were hashed as sources.
- **Content changes were never refused.** A real content change could be republished under the
  same label without notice.

## Options

1. **Keep the hash over every file.** Bump `network_version` on every register edit. Rejected: the
   label would churn with no change to the network.
2. **Hash the content only, and refuse content changes under an existing label.**

## Decision

**Option 2.**

- **Content is hashed.** The network hash is the SHA-256 over the network **content** files in
  schema order, each prefixed by its table name. The `network_version` metadata record is excluded
  (`publish.METADATA_TABLES`). Metadata can change without changing the hash: the register version,
  the validator and DQ reports, and the source hashes.
- **Twin outputs are not sources.** `ktv_psa_runtime_calibration.json` and
  `ktv_psa_runtime_reference_seconds.json` are computed from the network, so they leave the
  source-hash list (`publish.NOT_SOURCES`).
- **A content change means a new `network_version`.** `raildss network publish` compares the new
  content files with the publication already committed under the same label. If any differs, it
  refuses (exit 1, nothing written) and asks for a new `network.version` in the register. A new
  label publishes to a new directory. Metadata-only changes are written as before.
- **`--check` is unchanged.** The CI gate still compares every published file byte for byte, so
  stale metadata is caught too.

## Consequences

- The recorded hash of `KTV-PSA-2025-08` changes once, because the definition changed
  (`debbc0ad…` → `becc4f16…`). The content is identical, so the same-label check accepts it.
- A register edit still needs `raildss network publish` to refresh `network.json`, the provenance
  card and the manifest, but the network hash and label stay the same.
- A content change, such as an ETL fix or new derived inputs, needs a new `network.version` value
  and a new published directory. Code and tests that name `KTV-PSA-2025-08` move to it in the same
  PR.
- Tests:
  - A real parameter edit, assembled end to end, keeps the hash.
  - The hash ignores the metadata record but changes with content.
  - `raildss network publish` refuses a content change under the same label and writes nothing.

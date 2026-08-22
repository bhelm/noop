# Parity governance

This directory contains the product-free foundation for tracking Swift/Kotlin
parity. It inventories declarations and constants, records the current accepted
debt, and prevents regeneration from accepting new debt silently.

## Local checks

Run the complete Python tool suite, then the two repository gates:

```sh
python3 -m unittest discover -s Tools -p 'test*.py'
python3 Tools/parity_ledger.py
python3 Tools/parity_ratchet.py --base origin/main --offline
```

The ledger must report no finding beyond the checked-in baseline. The ratchet
also rescans the working tree, so editing a baseline without matching current
sources fails closed. `--offline` skips only GitHub existence checks; it does
not relax syntax, schema, current-tree, or exact-base checks.

The v3 twin map contains explicit source roots plus a count and SHA-256 for each
canonical semantic set. The full file/function/property/constant inventory is
derived deterministically from source; prose evidence and name-only suggestions
are not authority. A normal scan expands it and deep-checks every fingerprint.
The ratchet separately materializes and scans the exact base and current trees,
so regenerating both JSON files cannot hide a newly unpaired declaration. The ledger is
fail-closed when invoked; repository CI enforcement is intentionally deferred to the final
stack PR, alongside native execution and path-filtered workflow wiring.

Function pairs exactly equal resolved attached source claims. File pairs derive
from those claims before constant resolution, so stale file metadata cannot
steer constant pairing. Swift selector labels disambiguate overloads. Stale,
overlapping, duplicate, or ambiguous attached-pair structure is a hard scan
error and cannot be accepted by the baseline. A claim originating in an
authority root may resolve to an exact declaration in the wider repository
reference scope; that external endpoint is hashed into the pair authority but
does not silently widen the unpaired-inventory roots.

The compact baseline stores exact-identity-set hashes grouped by rule and narrow
source scope. Every group has a review reason and provenance; no wildcard or
umbrella issue matches findings.

When the final stack PR wires this gate into CI, that invocation must omit
`--offline`. Every governed `issue` field must use the exact
`owner/repository#number` form. Online validation derives the API path from
that value and verifies both the repository and issue number; pull requests do
not satisfy an issue reference. A new exact exemption additionally needs its
own fresh issue, a specific reason, and this marker in the issue body:

```text
parity-governance-identity-sha256: <the exemption's identity_sha256>
```

Issues cannot be reused across exemptions, and `bhelm/noop#17` is explicitly
forbidden as an umbrella.

## Updating the inventory

After an intentional source change:

```sh
python3 Tools/parity_ledger.py --bootstrap-map --write-baseline
python3 Tools/parity_ledger.py
python3 Tools/parity_ratchet.py --base origin/main --offline
```

Review the expanded semantic diff before retaining generated hashes. A new
unpaired item, a removed function/property/constant pair, or a new finding needs
an exact `exemptions[]` entry containing `kind`, `identity`, `identity_sha256`,
one fresh repository-qualified `issue`, and a narrow `reason`. Stale exemptions
fail. New exemptions require both a fresh issue and the exact identity-hash
marker; no bootstrap exemption or downgrade is accepted.
Do not preserve stale findings or freeze commit hashes in tests; repository
consistency is proved by independent rescanning and canonical set hashes.

## Layer boundary

This foundation has no module runner, case corpus, coverage report, Gradle,
SwiftPM, governance-gate workflow invocation, or native-test dependency. The
existing generic Tools test workflow only discovers its Python unit tests and
protects their count. Later layers can consume the inventory without making
this scanner depend on their orchestration.

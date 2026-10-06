# Base 2.2 native event relocation verification

This P10 scope covers `dwd_disclosure`, `dwd_disclosure_subject`, and
`dwd_shareholding_change`. It proves physical month routing and exact typed
preservation; it does not qualify supplier raw history, PIT, consumers, or a
complete Base 2.2 image.

The verifier accepts the current producer and the immutable producer at
`5ec44476835fc6fc32e87a9ef43c79111896642e`, whose module SHA256 is
`86259b087ccf2f1b333afb77fdae6b67612938d4e1043239ef00e33a2ff68a63`.
The latter retains its original descriptors, plans, receipts, and hashes.
Verification code identity is recorded separately; no producer evidence is
rewritten to represent a later verifier. Other producer hashes fail closed.
Incompatible future construction changes require a versioned contract.

Every physical row is checked against its Shanghai `effective_at` month. The
locked business and version keys contain `effective_at`, so equal full keys
cannot occur in two different months. Each month check includes every physical
file, compares full keys, and rejects null keys, duplicate versions, and
overlapping knowledge intervals. Direct bounded typed queries replace the
redundant 64-way key materialization for each already proven month. Foreign-key
targets remain global image-local parents; this optimization does not restrict
references to the child's partition or change temporal/source rules.

Failed attempts retain their original budgets, status, inputs, and evidence.
A resumed verification must freeze its actual producer/verifier/configuration
identities, complete file census, device capacity, operation order, and budgets
before writing. Successful previous checks may only be reused with their exact
scope and byte identities. A time or spill failure is not a data corruption
finding and is never converted to a passing receipt.

Existence-only event links now join exact global parent keys using bounded
physical child batches. This does not add temporal or supplier authority to the
relocation scope. Full temporal graph verification remains separate. Its compact
parent version maps are materialized once per referenced identity and reused;
every listing venue/version remains eligible under the original conditions.
Child batches retain all global parents and never replace whole-table key/version
checks. Batch membership is deterministic from frozen physical members and
actual footers (at most 256 files, 64 reader batches of rows, and one eighth of
the registered DuckDB memory in compressed source bytes). An oversized member
fails before that reference stage. Existing memory, RSS, spill and time gates
continue to apply; failed attempts are retained rather than reclassified.

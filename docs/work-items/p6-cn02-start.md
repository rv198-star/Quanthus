# P6-R1: CN02 Base 2.4 startup

| Field | Value |
| --- | --- |
| Parent | [Work-item index](index.md); [P6 design](../design/lld/08-validation-governance/p6-forward-validation.md) |
| Design status | approved: Owner requested P6 startup on 2026-10-22 |
| Delivery gate | open; restored rehearsal is distinct from official activation |
| Live work | P6-R1 / #511 and P6-ACTIVATE-001 / #209 |
| Source interface | [Base 2.4 input design](../design/lld/08-validation-governance/p6-base24-inputs.md) |

## Current recovery checkpoint

The startup branch is based on `f3eaf29d3f1313db910540dc13ebde988d148692`.
The CN02 P10 production worktree and image remain separate. Read the latest
node-local startup handoff for installed image, database and rehearsal identities;
this file does not assert an enabled service from repository configuration.

The preserved OCI PostgreSQL dump is 181,337 bytes, SHA256
`eda79142e3e0e0cabc845905caf1fe3945d49b4ce249141a00faf9a7f03c4843`.
It is a 2026-10-20 snapshot, not proof of current OCI state. Its ten P6 Jobs
contain seven successful disabled rehearsals and three failed official cycles.
Preserve those rows and every earlier activation; do not reset or relabel them.
No historical source snapshot may be counted as a new prospective observation.

## Implementation boundary

`forward_validation/base24_cycle.py` and the additive
`config/forward-validation/activation/p6-base24-cycle-runtime.v1.json` bind the
existing frozen policy files, consume P10's independently qualified runtime
windows and enter the existing paper-cycle engine through an explicit successor
adapter. They do not use the old Base 2.1/correction source paths. The legacy
runtime, entry proposal, experts, candidate rule, 66-session budget and 20-session
maturity tail remain unchanged.

Each qualified Daily envelope embeds independent physical input files, exact
input/code identities and the preceding real session. READY is last. The
same offline verifier checks the publication and the Worker read. A single
input copy and feature panel serve every in-Job consumer. Header-only adaptation
of the two proven-empty action tables supplies the frame reader's required
schema; it invents neither action rows nor unavailable provenance. Original
input bytes stay sealed. Nonempty action scopes require P10 qualification and
cannot enter this empty-table adapter.

The successor uses the already versioned after-close clock: actual availability,
observation end and decision are separate. An honest after-midnight decision may
precede the conservative 09:00 Shanghai deadline. Its access purpose and sealed
projection identify this profile; the legacy same-date access check is unchanged.
The existing P2 paper engine and the independent reproduction path are reused.

## Startup sequence and remaining hard boundaries

1. Preserve the current code/working trees and freeze an isolated disk/RSS budget.
2. Restore the retained database into a private Unix-socket-only PostgreSQL 16
   instance; expose no TCP port, revive no external credentials or old OCI service.
3. Preserve the exact historical P6 Job census, then execute a disabled rehearsal
   through the actual PostgreSQL `replay` queue and existing Replay Worker, with
   concurrency one, no market-data mounts and controller explicitly disabled.
4. Verify the real prepared Base 2.4 input and continuous frame interface without
   candidate/expert/order execution or historical result inspection.
5. Before enabling official admission, reconcile/fence the prior OCI P6 writer,
   bind the exact new source/runtime/deployment and new activation, and complete
   readiness and rollback evidence. Absence of NexusAgent is a control-channel
   limitation, not proof that OCI is offline or permission to revive a retired
   SSH control path.
6. Consume the first actual qualified Daily strictly after the new activation
   date, freeze one immutable intake/decision instant before queue submission,
   and reproduce the first legal official cycle independently before continuing.

The old October 20 runtime input became available on October 21 after its next
open deadline. It is suitable for input engineering, not a prospective cycle.
The current P10 Daily core and native 5m completion have separate acceptance:
P6's daily-only adapter need not wait for unrelated 5m delivery, but it still
needs the exact qualified P10 runtime handoff for its own target date.

The startup image is a local versioned build from the exact Git archive plus
registered dependency bytes. Its preactivation use has no source mounts, no
source network, no official controller, no notification, broker or live capital.
Starting it does not silently authorize missing gates. Public API/Web migration,
P3/P5, training and old-node shutdown are outside this startup.

## Checks

`tests/unit/test_p6_base24_cycle.py` covers separate runtime binding, old-reader
preservation, repacked physical/predecessor/visibility rejection, real shared
features and continuous frames, after-midnight access, first-cycle future-date
enforcement, immutable publication/reuse and complete synthetic paper-cycle
independent reproduction. Run applicable existing P6 suites and `make check`
before handoff. Real rehearsal success is engineering evidence; official budget
progress requires actual post-activation market days.

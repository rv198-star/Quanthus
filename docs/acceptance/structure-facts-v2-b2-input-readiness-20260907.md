# Facts v2 B2: real-DWD bounded input-readiness acceptance

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-004-B2` / #219 |
| Acceptance | ACCEPTED / CLOSED |
| Result-bearing revision | `723f22feeb7dfbecb2bffbc83662ced7bb29822d` |
| Run A | `job_5165e1bd99614b6186b58f228c770971` |
| Run B | `job_064e89c9ebbe4ed49badd8e07b78ea88` |
| Knowledge view | `retrospective` only |
| Execution authority | false |

## 1. Input and authority boundary

The accepted B2 lane uses the fixed request `config/structure-facts-v2-run.development.v1.json` and the bounded provider `config/replay-data-provider.structure-v2.development.v1.json` over the frozen DWD Base/correction.

The bounded projection is not the full P4 canonical projection. It is an explicit derivative of the accepted canonical parent authority. The ordinary canonical Direct-DWD path remains strict and unchanged.

Parent canonical evidence:

```text
config/p4-direct-dwd-canonical-projection.v1.json
file SHA-256: d0985d1454a25f7d3ad972b57d977410987b6a815b4809b2d8fe2e2d1b1e330a
receipt id: p4-direct-dwd-development-u16-20260824-v1
parent canonical bundle digest:
sha256:8142a2e5115de9a193c5282c14b531dbbcca73d84f933eded22a1354de17a39e
```

The derivative session validates the exact DWD Base/correction/materializer relation to that parent, then owns its bounded config/scope/content identity. It does not inherit the parent's canonical identity digest.

## 2. Exact real-data reproduction

Both successful Jobs used the same exact code revision and input digest:

```text
input digest:
sha256:2272dc751b1792dfa13584edbedfec4a9fa4373036ed5241d9b9c8cfe9235999

request digest:
sha256:c9a8082f351499ca7b2a2f0469d1c405c6e8b3ad5b428e7f9e48e0935316b92d

input-readiness digest:
sha256:ff61690b80bae61070440a83ee3683f458135a7555bbfe942b7453bc5c93f4e6

source-inputs.jsonl SHA-256:
sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac
```

The input-readiness digest includes the stable provider identity used by B2, including the bounded derivative provenance relation. Exact reproduction therefore covers both source-member closure and the provider lineage consumed by the admission report.

## 3. Admission denominator

Each successful run reported:

```text
report attempts: 174
admitted:       174
rejected:         0
knowledge view: retrospective
```

The physical source extraction produced 357 warm-up/report source-member rows before the report-scope denominator was reduced to the 174 configured security/session attempts.

The stronger historical views remain explicitly unclaimed:

```text
public reconstruction: not admitted
platform-as-known:     not admitted
per-decision publication proof: false
```

No missing publication timestamp is inferred from market date or from the existence of the retrospective DWD projection.

## 4. Resource and build evidence

Each Job built its bounded Direct-DWD prerequisite exactly once and reused it through B2-B5 and P2; cross-Job cache hits were zero.

```text
Run A Direct-DWD build: 51.163567 s
Run B Direct-DWD build: 50.666141 s
provider builds / Job: 1
cross-Job cache hits:  0
provider lifecycle:    job_local_TMP
```

This closes the earlier resource mismatch without turning the bounded TMP projection into a second canonical data product.

## 5. Gate disposition

| Gate | Result |
| --- | --- |
| Exact frozen source authority | PASS |
| Bounded derivative parent authority | PASS |
| Fixed development scope | PASS |
| Complete report denominator | PASS, 174/174 |
| Final price observations | PASS on admitted report rows |
| Required identity / coordinate evidence | PASS on admitted report rows |
| Missing-data reasons preserved | PASS; zero report rejections in this bounded sample |
| Silent historical view upgrade | REJECTED by contract |
| Real A/B input-readiness reproduction | PASS |

B2 acceptance is a bounded retrospective engineering/data-readiness claim only. It does not establish public/platform historical availability, economic value, production-expert quality or execution authority.

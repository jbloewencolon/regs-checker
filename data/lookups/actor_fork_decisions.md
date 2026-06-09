# Actor Fork Decisions — V1 (B0 Output)

**Status:** Locked (guide: "Moving Past B0", §2).
**Ratified by:** VC (pending sign-off on this doc per done-criteria §1.2).
**"For now" clause:** If re-harvest data shows over-granularity (e.g. `operator`
and `deployer` never diverge), change-control policy allows a later merge.

---

## F1 — data_handler split

**Question:** Keep one `data_handler` umbrella, or split into `controller` + `processor`?

**Decision: SPLIT.**

- `controller` — entity that determines purposes and means of data processing.
- `processor` — entity that processes data on behalf of and under instruction of a controller.

**Rationale:** Controller and processor carry materially different legal duties (lawful
basis, DPIAs, data-subject rights fall on the controller; sub-processor chain
requirements fall on the processor). Merging them would produce misleading obligation
counts in the trust check.

**Volume impact:** ~25% of extraction mentions (controller 359 + processor 133 + business 122).
The `business` term (122 mentions) is a sub-decision pending LKA ruling — see §Pending below.

---

## F2 — regulator vs government_agency

**Question:** Split enforcement/oversight bodies from government agencies that *use* AI?

**Decision: SPLIT.**

- `regulator` — government body with enforcement or oversight authority (attorney general,
  commission, enforcement agency, state/law-enforcement agency).
- `government_agency` — government body that procures, uses, or deploys AI (department,
  cabinet, legislature, unit). Government-as-deployer routes here, not to `regulator`.

**Rationale:** A government agency deploying a hiring tool has deployer obligations, not
enforcer obligations. Collapsing the two would misclassify actor duties.

**Volume impact:** ~16% of extraction mentions.

---

## F3 — individual (actor code vs protected-scope flag)

**Question:** `individual` as a Tier-1 actor code, or `actor_scope = protected` flag, or both?

**Decision: BOTH.**

- `individual` is retained as a Tier-1 code (person, consumer, applicant, minor, user).
- `actor_scope = protected` flag also applies — marks the individual as the *subject* of
  protections rather than a duty-bearer.

**Rationale:** Both dimensions are useful: the code enables actor-level queries; the flag
enables "who is protected" queries without misrepresenting the individual as an obligated
party.

**Volume impact:** ~9% of extraction mentions.

---

## F4 — operator vs deployer

**Question:** Merge, keep distinct, or treat `operator` as a Tier-2 alias of `deployer`?

**Decision: KEEP DISTINCT (for now).**

- `deployer` — entity that puts an AI system into operation in a specific use context.
- `operator` — entity that operates or manages a deployed AI system, often on behalf of
  a deployer (e.g. a managed-services provider running a client's AI system).

**Rationale:** `operator` (232 mentions, 9% of volume) is too large to subsume silently.
Several laws address operators distinctly. Lock now; revisit after re-harvest confirms
whether real co-occurrences diverge.

---

## developer/provider flag (fork-adjacent)

**Question:** Merge `developer` and `provider`, or keep distinct?

**Decision: KEEP DISTINCT.**

- `developer` — entity that creates or trains an AI system.
- `provider` — entity that supplies or makes available an AI system to others.

**Rationale:** Under EU framing "provider" ≈ developer; under US frameworks (e.g.
Colorado SB 205) developer and a supplying vendor are distinct roles with different
obligations. The crosswalk (`actor_crosswalk.csv`) documents tracker treatment
before any merge. No silent merge.

---

## Pending (not yet ratified)

### `business` (122 mentions) — LKA ruling required

Under CCPA framing "business" is the controller-equivalent → `controller`.
Used generically across many other laws it means `regulated_entity`.
**LKA must decide; record in `actor_aliases.csv`, not as a new fork.**
Until ruled, `business` routes to `PENDING_LKA` and is excluded from product output
(shown as `regulated_entity` provisionally per the unresolved-term routing rule).

---

*Source:* Engineering Guide "Moving Past B0" §2. Artifacts written to `data/lookups/` by V1 build.

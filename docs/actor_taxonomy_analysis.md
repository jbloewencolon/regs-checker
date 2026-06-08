# Actor Taxonomy: Analysis, Proposed Codes, and Recommendation

**Source:** Run 2026-05-10 → 2026-05-11. Fields: `obligation.subject_normalized` (1,834) + `compliance_mechanism.responsible_party_normalized` (725) = **2,559 actor mentions, 209 distinct values.**
**Attached:** `actor_value_to_code_full.csv` — every one of the 209 values, its count, tier breakdown, and proposed code.

---

## 1. What the model actually produced

The current `dim_actor_types` (4–6 supply-chain codes) captures less than half of real actor volume. The dominant categories are ones the supply-chain model never had:

| Proposed code | Mentions | % of volume | Distinct raw values | In current dim_actor_types? |
|---|---:|---:|---:|---|
| **data_handler** | 650 | 25.4% | 11 | ❌ new |
| deployer (incl. sector roles) | 510 | 19.9% | 49 | ✅ |
| **regulator / government** | 407 | 15.9% | 47 | ❌ new |
| **individual (protected party)** | 232 | 9.1% | 22 | ❌ new |
| operator | 232 | 9.1% | 1 | ✅ |
| developer | 214 | 8.4% | 3 | ✅ |
| provider | 114 | 4.5% | 18 | ✅ |
| **regulated_entity (generic)** | 65 | 2.5% | 9 | ❌ new |
| distributor | 8 | 0.3% | 1 | ✅ |
| compute_provider | ~0 | — | 0 | ✅ (unused here) |
| REVIEW — unclassified | 69 | 2.7% | 17 | committee |
| REVIEW — legal-process | 36 | 1.4% | 14 | likely not actors |
| INVALID — non-actor / garbled | 22 | 0.9% | 17 | data-quality fix |

**The headline:** your supply-chain codes (developer + deployer + operator + provider + distributor) cover ~42% of volume. The other ~58% is `data_handler`, `regulator/gov`, `individual`, and `regulated_entity` — none of which exist today. Your `data_handler` hypothesis was right: `controller` (359) + `processor` (133) + `business` (122) + variants = the single largest bucket at 25%.

---

## 2. Proposed `dim_actor_types` (extended)

From 4–6 codes to **~10 canonical codes**, sized to the data (not gold-plated — every code below clears 2.5% of volume except the two intentionally-kept supply-chain codes):

| code | display_label | absorbs (examples) | actor_scope default |
|---|---|---|---|
| `developer` | Developer | developer | primary |
| `deployer` | Deployer | deployer + **all sector-specific users** (employer, insurer, hospital, school, platform, publisher, advertiser, licensee…) → sector captured separately | primary |
| `operator` | Operator | operator | primary |
| `provider` | Provider | vendor, supplier, manufacturer, service provider, model-management company | primary |
| `distributor` | Distributor | distributor | primary |
| `compute_provider` | Compute Provider | cloud/compute provider | secondary |
| `data_handler` | Data Handler | **controller, processor, business**, data processor | primary |
| `data_broker` | Data Broker | data broker | primary |
| `regulator` | Regulator / Oversight Body | regulator, agency, department, commission, AG, enforcement authority | secondary |
| `individual` | Individual / Protected Person | person, individual, applicant, consumer, user, minor | **protected** |
| `regulated_entity` | Regulated Entity (generic) | regulated/covered entity, entity, third party | primary |

The full value→code assignments for all 209 values are in the attached CSV.

---

## 3. Four decisions this analysis surfaces (not engineering calls — committee/LKA rulings)

The mapping is mechanical for most values, but four clusters are genuine judgment calls the data can inform but not settle:

1. **Split `data_handler` or keep it merged?** `controller` and `processor` carry *different legal obligations* under privacy law. Merging them is simpler for matching; splitting preserves the legal distinction. The 25% volume makes this the highest-stakes call.
2. **`regulator` conflates two roles.** Some rows are *enforcement/oversight bodies* (AG, commission — they enforce). Others are *government agencies that deploy AI* (which are really `deployer` with a public-sector sector tag). "Who must comply" semantics differ. LKA should split these.
3. **`individual` is usually the *protected* party, not the regulated one.** It probably shouldn't be a compliance actor at all — it should ride on the existing `actor_scope = 'protected'` flag rather than imply an obligation. 9% of volume hinges on this.
4. **`operator` vs `deployer` overlap.** 232 `operator` mentions; in practice many are deployers. Keep distinct or fold? LKA ruling.

---

## 4. Data-quality finding (fix before mapping)

~5% of the field is not a usable actor: `INVALID` non-actors (`contract`, `document`, `website`, `program`) and **garbled strings** (`operat`, `socia`, `developer/deploy⎵⎵⎵⎵ployer`, `covered⇥entity` with a literal tab). Mapping these would enshrine extraction noise as vocabulary. The parse/extraction layer should be fixed so the field stops emitting non-actors and corrupted text — otherwise every future harvest re-imports the same junk.

---

## 5. My recommendation — how to proceed

In order:

**1. Pull Orrick/IAPP's own "covered entity" vocabulary *first* — before locking any codes.** This is the most important step and it follows directly from your trust model. Your bar is "matches Orrick/IAPP." If the trackers categorize entities as controller/processor/business, then `data_handler` should mirror their terms; if they use developer/deployer, map accordingly. **Choose the actor codes to maximize comparability with the trackers, not just internal tidiness.** This is a ~1-day lookup that changes what "correct" means for steps 2–4.

**2. Clean the field, then re-harvest.** Fix the ~5% junk/garble at the extraction/parse layer (small change), so the committee maps signal, not noise.

**3. Approve the ~10 canonical codes at committee — but defer the four §3 forks to LKA with this data in hand.** Don't let engineering silently decide controller-vs-processor or regulator-vs-gov-deployer; they're legal-semantic calls.

**4. Implement as the two-tier model from Strategy v2.** These ~10 are **Tier-1 canonical** (the matching key, profile-aligned). The rich labels (employer, insurer, hospital, AG, court…) stay as **Tier-2 descriptive**, each rolling up to a Tier-1 code, with sector context carried by the separate sector dimension — *not* as new actor codes.

**5. Re-confirm after the applicability run.** This distribution comes from one prompt version, and the bill-level applicability agent (which carries its own entity signal) hasn't run yet. Re-harvest once it has, and lock the codes only when two runs agree — pinned to `_prompt_hash` so a later prompt change forces a re-check.

**Net:** yes, extend `dim_actor_types` — to about **10 codes**, with `data_handler` and `regulator` as the two big additions your data demands. But sequence it so the codes are chosen *against the trackers* (step 1) and *on clean data* (step 2), because both feed directly into the trust check that defines the whole project.

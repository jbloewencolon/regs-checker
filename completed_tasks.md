# State AI Regulation Matrix — Completed Tasks

## Phase 1: Policy Navigator Schema Additions (Supabase MCP)
- [x] 1A: Extend dimension tables — added 4 legislative statuses (Vetoed, In Committee, Enjoined, Pending Signature), Compute Provider actor type, 3 requirement types (Bias Testing, Red Teaming, NIST Framework), 8 sector-level AI scope codes, widened scope_code to varchar(4)
- [x] 1B: Created `law_enforcement_details` table — per-law structured enforcement (private_right_of_action, max_civil_penalty_usd, cure_period_days)
- [x] 1C: Created `law_obligation_flags` table — per-law boolean matrix (bias testing, red teaming, NIST, assessments, audits, transparency, reporting). Bootstrapped from existing 373 map_law_requirements rows.
- [x] 1D: Created `law_triggering_thresholds` table — per-law compute FLOPS, sectors, exemptions. Bootstrapped from existing map_law_scopes.
- [x] 1E: Created `jurisdictional_conflicts` table + `conflict_type` enum (7 values)
- [x] 1F: Created `v_state_ai_regulation_matrix` view — assembles full matrix from fact_laws + 3 detail tables + conflicts. Verified working with live data.

## Phase 2: Regs Checker Pipeline Changes (Code)
- [x] 2A: Added `preemption_signal` extraction type — new enum value in models.py + Supabase, new PreemptionSignalPayload schema, new PreemptionAgent (src/agents/preemption.py), new YAML prompt (prompts/preemption.yml), Alembic migration
- [x] 2B: Extended ThresholdExceptionPayload with compute_flops, compute_description, sector_applicability fields. Updated threshold_exception.yml prompt.
- [x] 2C: Extended EnforcementInfo with max_civil_penalty_usd and cure_period_days. Updated obligation.yml prompt.
- [x] 2D: Extended ComplianceMechanismPayload with is_bias_testing, is_red_teaming, nist_measure_refs, assessment_frequency_months, is_third_party_audit, incident_reporting_hours. Updated compliance_mechanism.yml prompt.

## Phase 3: Sync Pipeline Changes (Code)
- [x] 3A: Updated payload_adapter.py — preserved structured enforcement booleans, added matrix fields for thresholds, added adapters for preemption_signal, rights_protection, and compliance_mechanism types
- [x] 3B: Created rollup_matrix.py — aggregates synced_extractions into 4 matrix detail tables with idempotent upserts

## Phase 4: Agent Grouping (Code)
- [x] 4: Added PreemptionAgent to extractor.py agent registry — runs in GPT group alongside 5 other GPT-based agents (no VRAM swap cost)

## Phase 5: UI Pipeline Integration
- [x] 5A: Fixed analytics.html — added type_colors for rights_protection, compliance_mechanism, preemption_signal
- [x] 5B: Fixed dashboard.html — updated agent count (4→7), model names (removed stale DeepSeek ref, updated to GPT-OSS 20B + Qwen 3.5 9B), updated setup instructions
- [x] 5C: Fixed review.html — added preemption_signal summary display (conflict_type + description)
- [x] 5D: Fixed v_state_ai_regulation_matrix view — changed INNER JOIN on dim_legislative_statuses to LEFT JOIN, recovering 124 laws with NULL status_id (48→172 rows)

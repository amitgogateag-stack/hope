from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_integrity_migration_enforces_duplicate_identity_guardrail():
    sql = (ROOT / "migrations/002_integrity_constraints.sql").read_text()
    assert "uq_identity_active_broker_instrument" in sql
    assert "WHERE status = 'ACTIVE'" in sql


def test_integrity_migration_prevents_historical_experiment_mutation():
    sql = (ROOT / "migrations/002_integrity_constraints.sql").read_text()
    assert "HOPE experiment history is immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON experiments" in sql


def test_experiment_invalidation_is_append_only_event():
    sql = (ROOT / "migrations/003_experiment_invalidation_events.sql").read_text()
    assert "experiment_invalidations" in sql
    assert "REFERENCES experiments(experiment_id)" in sql


def test_market_data_finalization_requires_manifest_backed_exact_coverage():
    sql = (ROOT / "migrations/068_market_data_finalization_integrity.sql").read_text()
    assert "hope_guard_market_data_version_finalization" in sql
    assert "MARKET_DATA_FINALIZATION_REQUIRES_COVERAGE_MANIFEST" in sql
    assert "MARKET_DATA_FINALIZATION_COVERAGE_MISMATCH" in sql
    assert "MARKET_DATA_FINALIZATION_REQUIRES_PIT_UNIVERSE" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_market_data_finalization_requires_valid_pit_universe_membership():
    sql = (ROOT / "migrations/069_market_data_finalization_membership.sql").read_text()
    assert "hope_guard_market_data_version_finalization" in sql
    assert "um.instrument_id = e.instrument_id" in sql
    assert "e.event_time >= um.valid_from" in sql
    assert "e.event_time < um.valid_to" in sql
    assert "MARKET_DATA_FINALIZATION_UNIVERSE_MEMBERSHIP_MISMATCH" in sql


def test_market_data_finalization_rejects_duplicate_manifest_coverage():
    sql = (ROOT / "migrations/070_market_data_manifest_unique_coverage.sql").read_text()
    assert "count(DISTINCT (instrument_id, event_time))" in sql
    assert "MARKET_DATA_FINALIZATION_DUPLICATE_MANIFEST_COVERAGE" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_market_data_finalization_preserves_provider_identity_continuity():
    sql = (
        ROOT / "migrations/071_market_data_manifest_identity_continuity.sql"
    ).read_text()
    assert "GROUP BY source, source_symbol" in sql
    assert "count(DISTINCT instrument_id) > 1" in sql
    assert "MARKET_DATA_FINALIZATION_IDENTITY_BINDING_CONFLICT" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_market_data_finalization_requires_canonical_provider_identity():
    sql = (
        ROOT / "migrations/072_market_data_manifest_identity_canonical.sql"
    ).read_text()
    assert "w->>'source' IS NULL" in sql
    assert "w->>'source' <> dataset_source" in sql
    assert "i->>'source_symbol' <> btrim(i->>'source_symbol')" in sql
    assert "MARKET_DATA_FINALIZATION_IDENTITY_NOT_CANONICAL" in sql


def test_market_data_finalization_preserves_request_window_contract():
    sql = (
        ROOT / "migrations/073_market_data_manifest_window_contract.sql"
    ).read_text()
    assert "interval_seconds')::integer <= 0" in sql
    assert "::timestamptz <=" in sql
    assert "EXTRACT(EPOCH FROM" in sql
    assert "[+-][0-9]{2}:[0-9]{2}" in sql
    assert "MARKET_DATA_FINALIZATION_WINDOW_CONTRACT_INVALID" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_market_data_finalization_requires_nonempty_manifest_structure():
    sql = (
        ROOT / "migrations/074_market_data_manifest_structure.sql"
    ).read_text()
    assert "jsonb_typeof(manifest_doc->'windows') <> 'array'" in sql
    assert "jsonb_array_length(manifest_doc->'windows') = 0" in sql
    assert "jsonb_typeof(w->'instruments') <> 'array'" in sql
    assert "jsonb_array_length(w->'instruments') = 0" in sql
    assert "MARKET_DATA_FINALIZATION_MANIFEST_STRUCTURE_INVALID" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_certified_research_evidence_is_bound_to_parent_run_identity():
    sql = (ROOT / "migrations/078_research_run_evidence_identity.sql").read_text()
    assert "guard_certified_research_run_evidence_identity" in sql
    assert "hope.certified-backtest-result.v2" in sql
    assert "research_provenance->>'experiment_id'" in sql
    assert "research_provenance->>'run_fingerprint'" in sql
    assert "RESEARCH_RUN_CERTIFIED_EVIDENCE_EXPERIMENT_MISMATCH" in sql
    assert "RESEARCH_RUN_CERTIFIED_EVIDENCE_RUN_FINGERPRINT_MISMATCH" in sql
    assert "BEFORE INSERT ON research_run_evidence" in sql


def test_certified_v3_evidence_is_bound_to_exact_parent_market_data_identity():
    sql = (ROOT / "migrations/080_research_run_evidence_market_data_identity.sql").read_text()
    assert "hope.certified-backtest-result.v3" in sql
    assert "research_run_market_data_provenance" in sql
    assert "research_provenance->>'dataset_version_id'" in sql
    assert "research_provenance->>'universe_version_id'" in sql
    assert "research_provenance->>'market_data_manifest_hash'" in sql
    assert "RESEARCH_RUN_CERTIFIED_EVIDENCE_DATASET_VERSION_MISMATCH" in sql
    assert "RESEARCH_RUN_CERTIFIED_EVIDENCE_UNIVERSE_VERSION_MISMATCH" in sql
    assert "RESEARCH_RUN_CERTIFIED_EVIDENCE_MANIFEST_HASH_MISMATCH" in sql



def test_experiment_variants_are_predeclared_comparable_and_immutable():
    sql = (ROOT / "migrations/081_experiment_variant_predeclaration.sql").read_text()
    assert "CREATE TABLE experiment_variants" in sql
    assert "guard_experiment_variant_predeclaration" in sql
    assert "EXPERIMENT_VARIANT_MUST_BE_PREDECLARED_BEFORE_RUNS" in sql
    assert "EXPERIMENT_VARIANT_DATASET_MISMATCH" in sql
    assert "EXPERIMENT_VARIANT_UNIVERSE_MISMATCH" in sql
    assert "EXPERIMENT_VARIANT_ENVIRONMENT_MISMATCH" in sql
    assert "EXPERIMENT_VARIANT_REQUIRES_MATERIAL_CHANGE" in sql
    assert "EXPERIMENT_VARIANT_IMMUTABLE" in sql



def test_research_decisions_require_comparable_completed_evidence_and_are_immutable():
    sql = (ROOT / "migrations/082_research_decisions.sql").read_text()
    assert "CREATE TABLE research_decisions" in sql
    assert "guard_research_decision_insert" in sql
    assert "RESEARCH_DECISION_CONTROL_RUN_MISMATCH" in sql
    assert "RESEARCH_DECISION_VARIANT_RUN_MISMATCH" in sql
    assert "RESEARCH_DECISION_AS_OF_MISMATCH" in sql
    assert "RESEARCH_DECISION_CONTROL_EVIDENCE_REQUIRED" in sql
    assert "RESEARCH_DECISION_VARIANT_EVIDENCE_REQUIRED" in sql
    assert "RESEARCH_DECISION_IMMUTABLE" in sql



def test_research_comparisons_bind_exact_evidence_and_gate_decisions():
    sql = (ROOT / "migrations/083_research_comparisons.sql").read_text()
    assert "CREATE TABLE research_comparisons" in sql
    assert "guard_research_comparison_insert" in sql
    assert "RESEARCH_COMPARISON_CONTROL_FINGERPRINT_MISMATCH" in sql
    assert "RESEARCH_COMPARISON_VARIANT_FINGERPRINT_MISMATCH" in sql
    assert "RESEARCH_COMPARISON_IMMUTABLE" in sql
    assert "RESEARCH_DECISION_COMPARISON_REQUIRED" in sql
    assert "CREATE OR REPLACE FUNCTION guard_research_decision_insert" in sql



def test_research_evaluation_plans_are_complete_predeclared_and_immutable():
    sql = (ROOT / "migrations/084_research_evaluation_plans.sql").read_text()
    assert "CREATE TABLE research_evaluation_plans" in sql
    assert "RESEARCH_EVALUATION_PLAN_MUST_PRECEDE_RUNS" in sql
    assert "regression_invariants" in sql
    assert "historical_evaluation" in sql
    assert "walk_forward" in sql
    assert "regime_analysis" in sql
    assert "parameter_sensitivity" in sql
    assert "cost_stress" in sql
    assert "slippage_stress" in sql
    assert "universe_perturbation" in sql
    assert "contribution_analysis" in sql
    assert "RESEARCH_EVALUATION_PLAN_IMMUTABLE" in sql
    assert "RESEARCH_COMPARISON_EVALUATION_PLAN_REQUIRED" in sql



def test_research_evaluation_results_complete_the_predeclared_protocol():
    sql = (ROOT / "migrations/085_research_evaluation_results.sql").read_text()
    assert "CREATE TABLE research_evaluation_results" in sql
    assert "RESEARCH_EVALUATION_RESULT_PROTOCOL_HASH_MISMATCH" in sql
    assert "RESEARCH_EVALUATION_RESULT_CONTROL_EVIDENCE_REQUIRED" in sql
    assert "RESEARCH_EVALUATION_RESULT_VARIANT_EVIDENCE_REQUIRED" in sql
    assert "RESEARCH_EVALUATION_RESULT_IMMUTABLE" in sql
    assert "RESEARCH_COMPARISON_EVALUATION_RESULTS_INCOMPLETE" in sql
    assert "completed_stage_count <> 9" in sql



def test_canonical_research_comparison_is_exact_projection_of_stage_evidence():
    sql = (ROOT / "migrations/086_canonical_research_comparison.sql").read_text()
    assert "hope.research-comparison.v2" in sql
    assert "jsonb_object_agg" in sql
    assert "evaluation_protocol_hash" in sql
    assert "evaluation_results" in sql
    assert "RESEARCH_COMPARISON_CANONICAL_EVIDENCE_MISMATCH" in sql



def test_research_decision_binds_exact_canonical_comparison():
    sql = (ROOT / "migrations/087_research_decision_comparison_binding.sql").read_text()
    assert "ADD COLUMN comparison_id UUID" in sql
    assert "ADD COLUMN comparison_fingerprint CHAR(64)" in sql
    assert "RESEARCH_DECISION_COMPARISON_BINDING_REQUIRED" in sql
    assert "RESEARCH_DECISION_COMPARISON_ID_MISMATCH" in sql
    assert "RESEARCH_DECISION_COMPARISON_FINGERPRINT_MISMATCH" in sql



def test_strategy_candidate_registry_is_dual_market_evidence_aware_and_immutable():
    sql = (ROOT / "migrations/088_strategy_candidate_registry.sql").read_text()
    assert "CREATE TABLE strategy_candidate_classifications" in sql
    assert "RESEARCH" in sql
    assert "BACKUP_CANDIDATE" in sql
    assert "OPERATIONAL_CANDIDATE" in sql
    assert "ARRAY['INDIA','USA']" in sql
    assert "research_decision_id" in sql
    assert "STRATEGY_CANDIDATE_DECISION_STRATEGY_MISMATCH" in sql
    assert "STRATEGY_CANDIDATE_CLASSIFICATION_IMMUTABLE" in sql



def test_strategy_candidate_current_state_and_capacity_are_deterministic():
    sql = (ROOT / "migrations/089_strategy_candidate_current_state.sql").read_text()
    assert "classification_sequence BIGSERIAL UNIQUE" in sql
    assert "CREATE VIEW current_strategy_candidate_classifications" in sql
    assert "ORDER BY scc.strategy_version_id, scc.classification_sequence DESC" in sql
    assert "STRATEGY_CANDIDATE_OPERATIONAL_CAPACITY_EXCEEDED" in sql
    assert "STRATEGY_CANDIDATE_BACKUP_CAPACITY_EXCEEDED" in sql



def test_strategy_family_catalog_is_immutable_and_regime_aware():
    sql = (ROOT / "migrations/090_strategy_family_catalog.sql").read_text()
    assert "CREATE TABLE strategy_family_catalog" in sql
    assert "CROSS_SECTIONAL_MOMENTUM" in sql
    assert "FIFTY_TWO_WEEK_HIGH" in sql
    assert "TIME_SERIES_TREND" in sql
    assert "LOW_VOLATILITY_DEFENSIVE" in sql
    assert "QUALITY_DEFENSIVE" in sql
    assert "MULTIFACTOR_QMV" in sql
    assert "BEAR_TREND" in sql
    assert "STRATEGY_FAMILY_CATALOG_IMMUTABLE" in sql

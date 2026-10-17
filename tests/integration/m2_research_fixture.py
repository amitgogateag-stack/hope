"""Controlled synthetic M2 scientific lineage; never a production promotion.

All data, deterministic backtest output, fingerprints, comparison and candidate
rows are persisted under normal guards. SUPPORTED is an explicit test reviewer
input: HOPE's existing decision model has no automatic profitability verdict.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from uuid import uuid4

from sqlalchemy import text
from hope.application.backtests.engine import DeterministicBacktest
from hope.application.experiments.comparisons import build_research_comparison, research_comparison_fingerprint
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES, ResearchEvaluationPlanDefinition
from hope.application.experiments.evaluation_results import ResearchEvaluationResultDefinition, research_evaluation_result_fingerprint
from hope.application.experiments.research_runs import research_run_provenance, research_result_fingerprint
from hope.domain.execution import CostModel, OrderSide
from hope.domain.market_data.models import MarketBar
from hope.domain.research.models import ResearchDecision, ResearchDecisionDefinition
from hope.domain.strategy.candidates import StrategyCandidateClassification, StrategyCandidateState, StrategyMarket
from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlanResolver, SqlAlchemyExecutionProvenanceRepository
from hope.infrastructure.repositories.experiments import ExperimentRecord, SqlAlchemyExperimentRepository
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository
from hope.infrastructure.repositories.research_comparisons import ResearchComparisonRecord, SqlAlchemyResearchComparisonRepository
from hope.infrastructure.repositories.research_decisions import SqlAlchemyResearchDecisionRepository
from hope.infrastructure.repositories.research_evaluation_plans import SqlAlchemyResearchEvaluationPlanRepository
from hope.infrastructure.repositories.research_evaluation_results import SqlAlchemyResearchEvaluationResultRepository
from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord, SqlAlchemyResearchRunEvidenceRepository
from hope.infrastructure.repositories.research_runs import ResearchRunRecord, SqlAlchemyResearchRunRepository
from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository
from hope.infrastructure.repositories.universe_snapshots import UniverseSnapshotRepository


def seed(connection, *, configuration=None, market=StrategyMarket.USA, as_of=None, include_quote=False, certified=True, backtest_override=None):
    """Return a fully persisted synthetic certified comparison + promoted version.

    Caller owns commit/rollback and migrations. No trigger, constraint, isolation
    level or database policy is disabled. Every fixture receives unique identity.
    """
    as_of = (as_of or datetime(2025, 9, 15, 14, tzinfo=UTC)).astimezone(UTC)
    strategy_id, strategy_version_id, instrument_id, dataset_id, dataset_version_id, universe_id, universe_version_id = (uuid4() for _ in range(7))
    exchange = "NYSE" if market is StrategyMarket.USA else "NSE"
    for sql, args in [
        ("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TIME_SERIES_TREND')", {"id": strategy_id, "name": f"M2-SYNTHETIC-{strategy_id}"}),
        ("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:strategy,'v1','m2-synthetic-fixture')", {"id": strategy_version_id, "strategy": strategy_id}),
        ("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) VALUES (:id,:name,:exchange,'ACTIVE')", {"id": instrument_id, "name": f"M2-{instrument_id}", "exchange": exchange}),
        ("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'TEST',TRUE)", {"id": dataset_id, "name": f"M2-{dataset_id}"}),
        ("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:id,:dataset,'v1','staging',FALSE)", {"id": dataset_version_id, "dataset": dataset_id}),
        ("INSERT INTO universes(universe_id,name) VALUES (:id,:name)", {"id": universe_id, "name": f"M2-{universe_id}"}),
        ("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:id,:universe,'v1',TRUE,1)", {"id": universe_version_id, "universe": universe_id}),
        ("INSERT INTO universe_members(universe_version_id,instrument_id,valid_from,valid_to) VALUES (:id,:instrument,:start,:end)", {"id": universe_version_id, "instrument": instrument_id, "start": as_of-timedelta(days=1), "end": as_of+timedelta(days=1)}),
    ]:
        connection.execute(text(sql), args)
    request = MarketDataRequest("TEST", ("ABC",), as_of-timedelta(minutes=1), as_of + (timedelta(minutes=1) if include_quote else timedelta(0)), timedelta(minutes=1))
    identities = {("TEST", "ABC"): instrument_id}
    SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(dataset_version_id, universe_version_id, (request,), identity_map=identities)
    bar = MarketBar(instrument_id=str(instrument_id), event_time=request.start, available_time=request.start,
        ingestion_time=request.start, open="100", high="101", low="99", close="100", volume="1000")
    bars = (bar,) if not include_quote else (bar, bar.model_copy(update={"event_time": as_of, "available_time": as_of, "ingestion_time": as_of}))
    SqlAlchemyMarketBarSink(connection).append(dataset_version_id, bars)
    SqlAlchemyMarketDataVersionFinalizer(connection).finalize(dataset_version_id, (request,), identity_map=identities)
    manifest_hash = connection.execute(text("SELECT manifest_hash FROM market_data_coverage_manifests WHERE dataset_version_id=:id"), {"id": dataset_version_id}).scalar_one()
    universe_snapshot = UniverseSnapshotRepository(connection).get(universe_version_id)

    variant_configuration = configuration if configuration is not None else {"risk": {"per_trade": "0.01"}, "cost_model": {"version": "m2-cost-v1", "commission_rate": "0.001", "slippage_bps": "2"}}
    control_configuration = {**variant_configuration, "synthetic_control_variant": "CONTROL"}
    config_values = [control_configuration, variant_configuration]
    experiment_ids = [f"M2-CONTROL-{uuid4()}", f"M2-VARIANT-{uuid4()}"]
    experiments, plans, runs, canonicals, result_hashes = [], [], [], [], []
    # The actual existing backtest engine produces this zero-trade evidence with
    # explicit nonzero transaction-cost configuration; no profitable edge is
    # fabricated to satisfy a threshold. This fixture tests reviewer authority.
    result = DeterministicBacktest(Decimal("1000"), CostModel("m2-cost-v1", Decimal("0.001"), Decimal("2"))).run(
        bars, lambda _: None, lambda _: None, OrderSide.BUY)
    for experiment_id, config in zip(experiment_ids, config_values):
        config_hash = configuration_hash(config)
        connection.execute(text("INSERT INTO configuration_snapshots(configuration_hash,canonical_json) VALUES (:hash,CAST(:value AS JSONB)) ON CONFLICT(configuration_hash) DO NOTHING"), {"hash": config_hash, "value": json.dumps(config, sort_keys=True, separators=(",", ":"))})
        experiment = ExperimentRecord(experiment_id=experiment_id, hypothesis="Controlled synthetic M2 authorization lineage",
            strategy_version_id=strategy_version_id, dataset_version_id=dataset_version_id, universe_version_id=universe_version_id,
            configuration_hash=config_hash, environment="BACKTEST", status="CREATED")
        SqlAlchemyExperimentRepository(connection).create(experiment)
        experiments.append(experiment)
    connection.execute(text("INSERT INTO experiment_variants(variant_experiment_id,control_experiment_id,variant_label) VALUES (:variant,:control,'synthetic-M2-fixture')"), {"variant": experiment_ids[1], "control": experiment_ids[0]})
    protocol = {stage: {"enabled": True, "synthetic_fixture": True} for stage in REQUIRED_EVALUATION_STAGES}
    plan_repo = SqlAlchemyResearchEvaluationPlanRepository(connection)
    plan_repo.persist(ResearchEvaluationPlanDefinition(variant_experiment_id=experiment_ids[1], protocol=protocol))
    protocol_hash = plan_repo.get(experiment_ids[1]).protocol_hash
    run_repo = SqlAlchemyResearchRunRepository(connection)
    for experiment in experiments:
        execution = CertifiedExecutionPlanResolver(SqlAlchemyExecutionProvenanceRepository(connection)).resolve(experiment)
        plans.append(execution)
        provenance = research_run_provenance(experiment, as_of=as_of, universe_snapshot=universe_snapshot, market_data_manifest_hash=manifest_hash)
        run_id = run_repo.deterministic_id(experiment.experiment_id, provenance["run_fingerprint"])
        run_repo.claim(ResearchRunRecord(research_run_id=run_id, experiment_id=experiment.experiment_id, run_fingerprint=provenance["run_fingerprint"], as_of=as_of))
        canonical, result_hash = research_result_fingerprint(result, execution_plan=execution, research_provenance=provenance)
        if backtest_override is not None:
            # Explicit counterfeit negative fixture only; retain all DB guards
            # and recompute the real content hash rather than forging identity.
            canonical["backtest"] = backtest_override
            canonical, result_hash = research_result_fingerprint(canonical)
        if not certified:
            # Legitimate generic research storage is deliberately insufficient
            # for the certified PAPER bridge, even with matching run hashes.
            canonical, result_hash = research_result_fingerprint({"research_provenance": provenance, "generic_fixture": True})
        SqlAlchemyResearchRunEvidenceRepository(connection).persist(ResearchRunEvidenceRecord(research_run_id=run_id, result_fingerprint=result_hash, canonical_result=canonical))
        runs.append(run_id)
        canonicals.append(canonical)
        result_hashes.append(result_hash)
    result_repo = SqlAlchemyResearchEvaluationResultRepository(connection)
    for stage in REQUIRED_EVALUATION_STAGES:
        evidence = {"stage": stage, "status": "RECORDED", "synthetic_fixture": True,
            "cost_inclusive": True, "control_total_return": "0", "variant_total_return": "0"}
        result_repo.persist(ResearchEvaluationResultDefinition(variant_experiment_id=experiment_ids[1], control_run_id=runs[0], variant_run_id=runs[1], stage=stage, protocol_hash=protocol_hash, canonical_result=evidence, result_fingerprint=research_evaluation_result_fingerprint(evidence)))
    stage_records = result_repo.list_by_run_pair(experiment_ids[1], runs[0], runs[1])
    comparison = build_research_comparison(variant_experiment_id=experiment_ids[1], control_run_id=runs[0], variant_run_id=runs[1], control_result_fingerprint=result_hashes[0], variant_result_fingerprint=result_hashes[1], protocol_hash=protocol_hash, stage_results={record.stage: {"result_fingerprint": record.result_fingerprint, "result": record.canonical_result} for record in stage_records})
    comparison_hash = research_comparison_fingerprint(comparison)
    comparisons = SqlAlchemyResearchComparisonRepository(connection)
    comparison_id = comparisons.deterministic_id(variant_experiment_id=experiment_ids[1], control_run_id=runs[0], variant_run_id=runs[1], control_result_fingerprint=result_hashes[0], variant_result_fingerprint=result_hashes[1], comparison_fingerprint=comparison_hash)
    comparisons.persist(ResearchComparisonRecord(comparison_id=comparison_id, variant_experiment_id=experiment_ids[1], control_run_id=runs[0], variant_run_id=runs[1], control_result_fingerprint=result_hashes[0], variant_result_fingerprint=result_hashes[1], comparison_fingerprint=comparison_hash, canonical_comparison=comparison))
    decision_id = f"M2-TEST-REVIEWER-SUPPORTED-{uuid4()}"
    SqlAlchemyResearchDecisionRepository(connection).create(ResearchDecisionDefinition(decision_id=decision_id, variant_experiment_id=experiment_ids[1], control_run_id=runs[0], variant_run_id=runs[1], comparison_id=comparison_id, comparison_fingerprint=comparison_hash, decision=ResearchDecision.SUPPORTED, rationale="Controlled synthetic reviewer decision solely for authorization testing; not profitability evidence"))
    definition = StrategyCandidateClassification(strategy_version_id=strategy_version_id, markets=frozenset({market}), state=StrategyCandidateState.OPERATIONAL_CANDIDATE, research_decision_id=decision_id, rationale="Controlled synthetic M2 promotion; never production permission")
    candidates = SqlAlchemyStrategyCandidateRepository(connection)
    assert candidates.append(definition)
    classification_id = candidates.deterministic_id(definition)
    return dict(as_of=as_of, bars=bars, request=request, exchange=exchange, strategy_id=strategy_id, strategy_version_id=strategy_version_id,
        strategy_version="v1", family="TIME_SERIES_TREND", code_commit="m2-synthetic-fixture",
        market=market, instrument_id=instrument_id, dataset_version_id=dataset_version_id,
        universe_version_id=universe_version_id, manifest_hash=manifest_hash,
        classification_id=classification_id, research_decision_id=decision_id,
        control_experiment_id=experiment_ids[0], variant_experiment_id=experiment_ids[1],
        control_run_id=runs[0], variant_run_id=runs[1], source_research_run_id=runs[1],
        configuration=variant_configuration, execution_plan=plans[1], canonical_result=canonicals[1],
        comparison_id=comparison_id, comparison_fingerprint=comparison_hash)

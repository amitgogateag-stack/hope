from uuid import uuid4

import pytest

from hope.application.experiments.execution import (
    CertifiedResearchExecutor,
    ResearchExecutionImplementation,
    ResearchExecutionRegistry,
)
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


def _plan():
    return CertifiedExecutionPlan(
        experiment_id="exp-registry",
        strategy_version_id=uuid4(),
        strategy_id=uuid4(),
        strategy_version="1.2.3",
        code_commit="abc123",
        configuration_hash="a" * 64,
        configuration={"lookback": 20},
    )


def _implementation(plan, execute=lambda plan, context: (plan, context), **changes):
    values = {
        "strategy_version_id": plan.strategy_version_id,
        "strategy_id": plan.strategy_id,
        "strategy_version": plan.strategy_version,
        "code_commit": plan.code_commit,
        "execute": execute,
    }
    values.update(changes)
    return ResearchExecutionImplementation(**values)


def test_certified_executor_runs_only_exact_registered_implementation() -> None:
    plan = _plan()
    calls = []

    def execute(resolved_plan, context):
        calls.append((resolved_plan, context))
        return {"ok": True}

    executor = CertifiedResearchExecutor(
        ResearchExecutionRegistry([_implementation(plan, execute=execute)])
    )
    context = {"pit": True}
    assert executor.execute(plan, context) == {"ok": True}
    assert calls == [(plan, context)]


def test_registry_rejects_missing_and_duplicate_strategy_version_identity() -> None:
    plan = _plan()
    with pytest.raises(RuntimeError, match="RESEARCH_IMPLEMENTATION_NOT_REGISTERED"):
        CertifiedResearchExecutor(ResearchExecutionRegistry([])).execute(plan, object())

    implementation = _implementation(plan)
    with pytest.raises(ValueError, match="DUPLICATE_STRATEGY_VERSION_ID"):
        ResearchExecutionRegistry([implementation, implementation])


def test_registry_fails_closed_on_strategy_or_commit_identity_mismatch() -> None:
    plan = _plan()
    cases = (
        ({"strategy_id": uuid4()}, "STRATEGY_ID_MISMATCH"),
        ({"strategy_version": "9.9.9"}, "STRATEGY_VERSION_MISMATCH"),
        ({"code_commit": "different"}, "CODE_COMMIT_MISMATCH"),
    )
    for changes, error in cases:
        executor = CertifiedResearchExecutor(
            ResearchExecutionRegistry([_implementation(plan, **changes)])
        )
        with pytest.raises(ValueError, match=error):
            executor.execute(plan, object())


def test_registry_validates_canonical_definition_and_callable() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="STRATEGY_VERSION_NOT_CANONICAL"):
        _implementation(plan, strategy_version=" 1.2.3")
    with pytest.raises(ValueError, match="CODE_COMMIT_NOT_CANONICAL"):
        _implementation(plan, code_commit=" abc123")
    with pytest.raises(TypeError, match="EXECUTOR_MUST_BE_CALLABLE"):
        _implementation(plan, execute=object())
    with pytest.raises(TypeError, match="RESEARCH_IMPLEMENTATION_REQUIRED"):
        ResearchExecutionRegistry([object()])


def test_certified_executor_rejects_unsealed_inputs() -> None:
    plan = _plan()
    implementation = _implementation(plan)
    with pytest.raises(TypeError, match="REQUIRES_REGISTRY"):
        CertifiedResearchExecutor(object())
    executor = CertifiedResearchExecutor(ResearchExecutionRegistry([implementation]))
    with pytest.raises(TypeError, match="REQUIRES_EXECUTION_PLAN"):
        executor.execute(object(), object())

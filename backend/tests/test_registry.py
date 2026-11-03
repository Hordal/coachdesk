from __future__ import annotations

import pytest

from app.pipeline.registry import MANUAL_EXECUTED, RunMode, StageRegistry, StageSource
from app.pipeline.stages import STAGE_ORDER, StageName, StageRequest, StageResponse


class FakeStage:
    def __init__(self, name: StageName) -> None:
        self.name = name
        self.implementation = "fake"
        self.version = "0"

    def run(self, request: StageRequest) -> StageResponse:
        return StageResponse(run_id=request.run_id, stage=self.name, status="completed")


def test_stage_order_covers_the_twelve_stages_in_flow_order():
    assert len(STAGE_ORDER) == 12
    assert STAGE_ORDER[0] is StageName.VIDEO_READER
    assert STAGE_ORDER[-1] is StageName.FINDING_BUILDER
    assert STAGE_ORDER.index(StageName.CONTACT_JUDGE) < STAGE_ORDER.index(StageName.AGGREGATOR)


def test_fixture_mode_uses_fixtures_for_every_stage():
    plan = StageRegistry().plan(RunMode.FIXTURE)
    assert set(plan.values()) == {StageSource.FIXTURE}


def test_manual_mode_executes_only_aggregation_and_findings():
    plan = StageRegistry().plan(RunMode.MANUAL)
    executed = {n for n, s in plan.items() if s is StageSource.IMPLEMENTED}
    assert executed == set(MANUAL_EXECUTED)
    assert all(s is StageSource.MANUAL for n, s in plan.items() if n not in MANUAL_EXECUTED)


def test_partial_mode_replaces_only_registered_stages():
    reg = StageRegistry()
    reg.register(FakeStage(StageName.POSE_ESTIMATOR))
    plan = reg.plan(RunMode.PARTIAL)
    assert plan[StageName.POSE_ESTIMATOR] is StageSource.IMPLEMENTED
    assert plan[StageName.CONTACT_JUDGE] is StageSource.MANUAL


def test_full_mode_requires_every_stage():
    reg = StageRegistry()
    with pytest.raises(LookupError):
        reg.plan(RunMode.FULL)
    for name in STAGE_ORDER:
        reg.register(FakeStage(name))
    assert set(reg.plan(RunMode.FULL).values()) == {StageSource.IMPLEMENTED}

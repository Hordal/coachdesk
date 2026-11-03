"""단계 구현 선택과 실행 모드(상세 설계 27의 3.3)."""

from __future__ import annotations

from enum import StrEnum

from .stages import STAGE_ORDER, Stage, StageName


class RunMode(StrEnum):
    FIXTURE = "fixture"  # 모든 단계를 가상 예제로 대체
    MANUAL = "manual"    # 사람이 입력한 사건·판정으로 집계·문장 단계만 실행
    PARTIAL = "partial"  # 완성된 단계만 실제 구현, 나머지는 manual 입력
    FULL = "full"        # 모든 단계 실제 구현


class StageSource(StrEnum):
    FIXTURE = "fixture"    # 가상 예제가 공급
    MANUAL = "manual"      # 사람이 입력한 결과가 공급
    IMPLEMENTED = "implemented"  # 등록된 구현이 실행


# manual 모드에서 실제로 실행하는 단계(나머지는 입력 결과가 대신한다)
MANUAL_EXECUTED = frozenset({StageName.AGGREGATOR, StageName.FINDING_BUILDER})


class StageRegistry:
    def __init__(self) -> None:
        self._impls: dict[StageName, Stage] = {}

    def register(self, stage: Stage) -> None:
        self._impls[stage.name] = stage

    def get(self, name: StageName) -> Stage | None:
        return self._impls.get(name)

    def plan(self, mode: RunMode) -> dict[StageName, StageSource]:
        """모드별로 각 단계의 결과를 누가 공급하는지 돌려준다."""
        plan: dict[StageName, StageSource] = {}
        for name in STAGE_ORDER:
            if mode is RunMode.FIXTURE:
                plan[name] = StageSource.FIXTURE
            elif mode is RunMode.MANUAL:
                plan[name] = StageSource.IMPLEMENTED if name in MANUAL_EXECUTED else StageSource.MANUAL
            elif mode is RunMode.PARTIAL:
                plan[name] = StageSource.IMPLEMENTED if name in self._impls else StageSource.MANUAL
            else:
                if name not in self._impls:
                    raise LookupError(f"full 모드에 필요한 구현이 없음: {name}")
                plan[name] = StageSource.IMPLEMENTED
        return plan

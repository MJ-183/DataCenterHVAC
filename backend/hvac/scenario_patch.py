"""
시나리오 변경분(patch) 적용 - 자연어 시나리오(Phase 4.5)에서 사용.

LLM은 "어느 경로를 어떻게 바꿀지"만 제안하고, 실제 값 계산(곱셈/덧셈)과 검증은 여기서 한다.
경로 문법: 점 구분, 리스트 인덱스는 숫자 또는 '*'(전체)
  예) it_load.design_kw / envelope.walls.*.u_value / it_load.utilization (+ hours=[13,14])
"""
import copy
import math
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .schemas import HOURS, Scenario

Op = Literal["set", "multiply", "add"]


class FieldChange(BaseModel):
    path: str = Field(description="변경할 필드 경로. 예: it_load.design_kw, envelope.walls.*.u_value")
    op: Op = Field(description="set=값으로 교체, multiply=현재값에 곱함, add=현재값에 더함")
    value: float
    hours: list[int] = Field(description="24시간 배열 필드일 때 적용할 시각(0~23). 빈 리스트면 전체 시간. 스칼라 필드는 빈 리스트")
    reason: str = Field(description="요청 문장 중 근거가 된 부분")


class ScenarioEdit(BaseModel):
    """LLM 구조화 출력 스키마"""
    changes: list[FieldChange]
    unsupported: list[str] = Field(description="시나리오 필드로 표현할 수 없어 반영하지 못한 요청")
    summary: str = Field(description="변경 내용 한 줄 요약 (한국어)")


class AppliedChange(BaseModel):
    path: str          # 구체 경로 (예: envelope.walls.2.u_value, it_load.utilization.14)
    old: float
    new: float
    reason: str


class PatchError(ValueError):
    pass


def numeric_paths(sc: dict, prefix: str = "") -> list[str]:
    """시나리오 내 수정 가능한 숫자 필드 경로 (24시간 배열은 배열 경로 하나로)"""
    out = []
    for k, v in sc.items():
        if k.startswith("_"):
            continue
        p = f"{prefix}{k}"
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.append(p)
        elif isinstance(v, dict):
            out += numeric_paths(v, p + ".")
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            out.append(p)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out += numeric_paths(item, f"{p}.{i}.")
    return out


def _expand(node, parts, path=""):
    """'*' 를 펼쳐 (부모 컨테이너, 키, 구체경로) 목록을 반환"""
    head, rest = parts[0], parts[1:]
    if isinstance(node, list):
        if head == "*":
            keys = range(len(node))
        elif head.isdigit() and int(head) < len(node):
            keys = [int(head)]
        else:
            raise PatchError(f"잘못된 인덱스 '{head}' (경로 {path or '/'})")
    elif isinstance(node, dict):
        if head not in node or head.startswith("_"):
            raise PatchError(f"존재하지 않는 필드 '{head}' (경로 {path or '/'})")
        keys = [head]
    else:
        raise PatchError(f"'{path}' 아래로 더 들어갈 수 없습니다")
    out = []
    for k in keys:
        p = f"{path}.{k}" if path else str(k)
        if rest:
            out += _expand(node[k], rest, p)
        else:
            out.append((node, k, p))
    return out


def _apply_op(old: float, op: Op, value: float) -> float:
    new = {"set": value, "multiply": old * value, "add": old + value}[op]
    if not math.isfinite(new):
        raise PatchError("계산 결과가 유한한 숫자가 아닙니다")
    return round(new, 6)


def apply_changes(scenario: dict, changes: list[FieldChange]) -> tuple[dict, list[AppliedChange]]:
    """변경분을 적용한 새 시나리오와 실제 변경 목록을 반환. 경로/값 오류는 PatchError, 범위 오류는 ValidationError"""
    sc = copy.deepcopy(scenario)
    applied: list[AppliedChange] = []
    for ch in changes:
        targets = _expand(sc, ch.path.strip().split("."))
        for parent, key, concrete in targets:
            cur = parent[key]
            if isinstance(cur, list):
                if len(cur) != HOURS or not all(isinstance(x, (int, float)) for x in cur):
                    raise PatchError(f"'{concrete}' 는 숫자 필드가 아닙니다")
                bad = [h for h in ch.hours if not 0 <= h < HOURS]
                if bad:
                    raise PatchError(f"hours 범위 오류 {bad} (0~23)")
                for h in (ch.hours or range(HOURS)):
                    new = _apply_op(cur[h], ch.op, ch.value)
                    applied.append(AppliedChange(path=f"{concrete}.{h}", old=cur[h], new=new, reason=ch.reason))
                    cur[h] = new
            elif isinstance(cur, (int, float)) and not isinstance(cur, bool):
                if ch.hours:
                    raise PatchError(f"'{concrete}' 는 시간 배열이 아니므로 hours 를 지정할 수 없습니다")
                new = _apply_op(cur, ch.op, ch.value)
                applied.append(AppliedChange(path=concrete, old=cur, new=new, reason=ch.reason))
                parent[key] = new
            else:
                raise PatchError(f"'{concrete}' 는 숫자 필드가 아닙니다")
    Scenario.model_validate(sc)   # 범위/ΔT/COC 검증 (실패 시 ValidationError)
    return sc, [a for a in applied if a.old != a.new]


__all__ = ["FieldChange", "ScenarioEdit", "AppliedChange", "PatchError",
           "numeric_paths", "apply_changes", "ValidationError"]

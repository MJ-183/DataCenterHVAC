"""
AI 해설 / 자연어 시나리오 (Phase 4.5, 선택 기능).

- backend/.env 의 OPENAI_API_KEY, OPENAI_MODEL 로만 설정. 키는 프론트로 절대 보내지 않는다.
- 둘 중 하나라도 없으면 /ai/status 가 enabled=false 를 반환하고 /explain, /scenario/parse 는 503.
- 계산은 하지 않는다: 해설은 hvac.insights 가 계산한 값만 인용, 시나리오 변경은 hvac.scenario_patch 가 적용.
"""
import json
from functools import lru_cache
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from hvac import secrets
from hvac.insights import compute_facts, hourly_only, unverified_numbers
from hvac.scenario_patch import AppliedChange, PatchError, ScenarioEdit, apply_changes, numeric_paths
from hvac.schemas import Scenario, SimulationResult

ENV_PATH = None   # None 이면 secrets.ENV_PATH (테스트에서 바꿈)

router = APIRouter(tags=["ai"])


class LLM(Protocol):
    model: str

    def text(self, system: str, user: str) -> str: ...
    def parse(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel: ...


class OpenAILLM:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, timeout=60)
        self.model = model

    def _call(self, fn, **kw):
        from openai import OpenAIError
        try:
            return fn(model=self.model, **kw)
        except OpenAIError as e:
            raise HTTPException(502, f"OpenAI 호출 실패: {e}")

    def text(self, system, user):
        r = self._call(self.client.chat.completions.create,
                       messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content or ""

    def parse(self, system, user, schema):
        r = self._call(self.client.chat.completions.parse, response_format=schema,
                       messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        msg = r.choices[0].message
        if msg.parsed is None:
            raise HTTPException(502, f"모델이 구조화 출력을 반환하지 않았습니다: {msg.refusal or '응답 없음'}")
        return msg.parsed


def ai_config() -> tuple[str | None, str | None]:
    # 매 호출마다 .env 를 다시 읽음 (VS Code 에서 저장하면 서버 재시작 없이 반영)
    return secrets.get("OPENAI_API_KEY", ENV_PATH), secrets.get("OPENAI_MODEL", ENV_PATH)


@lru_cache(maxsize=1)
def _client(key: str, model: str) -> OpenAILLM:
    return OpenAILLM(key, model)


def get_llm() -> LLM:
    key, model = ai_config()
    if not key or not model:
        raise HTTPException(503, "AI 기능 비활성: backend/.env 에 OPENAI_API_KEY 와 OPENAI_MODEL 을 설정하세요")
    return _client(key, model)


@router.get("/ai/status")
def ai_status():
    key, model = ai_config()
    missing = [n for n, v in (("OPENAI_API_KEY", key), ("OPENAI_MODEL", model)) if not v]
    return {"enabled": not missing, "model": model if not missing else None,
            "reason": None if not missing else f"backend/.env 에 {', '.join(missing)} 미설정"}


# ---------- 해설 ----------
EXPLAIN_SYSTEM = """당신은 데이터센터 냉방 설비 엔지니어입니다. 사용자가 준 시뮬레이션 결과를 한국어로 해설합니다.
규칙:
- 제공된 결과 JSON(facts, result)의 숫자만 인용하고, 없는 값은 추정하지 말 것.
- 직접 사칙연산을 하지 말 것. 비중·피크·최소/최대는 facts 에 계산되어 있으니 그대로 인용할 것.
- 숫자는 JSON 의 값과 단위를 그대로 쓸 것 (단위 환산·반올림 변경 금지).
- 데이터에 없는 항목(예: 비용, PUE, 펌프 전력)은 "모델에 없음"이라고만 말할 것.
- 구성: 1) 한 줄 요약 2) 부하 피크와 구성 비중 3) 외기 조건과 칠러 COP 변화 4) 냉수·냉각수 5) 보급수 6) 유의사항
- 마크다운 목록으로 400~700자 내외."""


class ExplainResponse(BaseModel):
    text: str
    model: str
    facts: dict
    unverified_numbers: list[str]


@router.post("/explain", response_model=ExplainResponse)
def explain(result: SimulationResult, llm: LLM = Depends(get_llm)):
    full = result.model_dump()
    facts = compute_facts(full)          # 요약 지표는 전체 스텝으로 계산
    raw = hourly_only(full)              # 모델에는 정시 값만 전달 (5분 간격 전체는 너무 큼)
    user = ("다음 시뮬레이션 결과를 해설하세요.\n"
            f"facts(코드로 계산한 요약 지표, {full['step_min']}분 간격 전체 기준):\n"
            f"{json.dumps(facts, ensure_ascii=False)}\n\n"
            f"result(정시 원본 결과):\n{json.dumps(raw, ensure_ascii=False)}")
    text = llm.text(EXPLAIN_SYSTEM, user)
    return ExplainResponse(text=text, model=llm.model, facts=facts,
                           unverified_numbers=unverified_numbers(text, facts, raw))


# ---------- 자연어 시나리오 ----------
PARSE_SYSTEM = """당신은 데이터센터 HVAC 시뮬레이터의 시나리오 편집기입니다.
사용자의 한국어 요청을 현재 시나리오 JSON 에 대한 변경 목록(changes)으로 바꿉니다.
규칙:
- path 는 반드시 아래 '수정 가능한 경로' 중 하나이거나, 리스트 인덱스를 '*' 로 바꾼 형태(예: envelope.walls.*.u_value).
- "20% 증설" 같은 상대 변경은 op="multiply", value=1.2 처럼 표현하고 직접 계산하지 말 것. 절대값은 op="set".
- 24시간 배열(weather.*, it_load.utilization)은 hours 에 적용 시각(0~23)을 넣고, 전체 시간이면 빈 리스트.
- 스칼라 필드는 hours=[].
- 시나리오 필드로 표현할 수 없는 요청(예: 칠러 대수 추가, 프리쿨링)은 changes 에 넣지 말고 unsupported 에 적을 것.
- 요청에 없는 값은 바꾸지 말 것."""


class ParseRequest(BaseModel):
    text: str
    scenario: Scenario


class ParseResponse(BaseModel):
    summary: str
    unsupported: list[str]
    changes: list[AppliedChange]
    scenario: Scenario


@router.post("/scenario/parse", response_model=ParseResponse, response_model_by_alias=True)
def parse_scenario(req: ParseRequest, llm: LLM = Depends(get_llm)):
    base = req.scenario.to_engine_dict()
    user = (f"수정 가능한 경로:\n{json.dumps(numeric_paths(base), ensure_ascii=False)}\n\n"
            f"현재 시나리오:\n{json.dumps(base, ensure_ascii=False)}\n\n요청: {req.text}")
    edit = llm.parse(PARSE_SYSTEM, user, ScenarioEdit)
    try:
        edit = ScenarioEdit.model_validate(edit.model_dump())
        new, applied = apply_changes(base, edit.changes)
    except PatchError as e:
        raise HTTPException(422, {"message": f"변경분 적용 실패: {e}", "proposed": edit.model_dump()})
    except ValidationError as e:
        errs = [{"loc": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()]
        raise HTTPException(422, {"message": "변경 후 시나리오가 검증에 실패했습니다", "errors": errs,
                                  "proposed": edit.model_dump()})
    return ParseResponse(summary=edit.summary, unsupported=edit.unsupported, changes=applied,
                         scenario=Scenario.model_validate(new))

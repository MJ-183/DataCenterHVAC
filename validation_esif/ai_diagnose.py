"""
ESIF 비교 결과의 오차 원인을 OpenAI 로 정리 (계산은 하지 않음 — 이미 계산된 근거만 읽고 원인 후보를 순위화)

실행 (프로젝트 루트, PC 에서):  python validation_esif/ai_diagnose.py
        프롬프트만 보기:          python validation_esif/ai_diagnose.py --dry-run
필요: backend/.env 의 OPENAI_API_KEY, OPENAI_MODEL (구조화 출력 지원 모델), esif_compare.py·sensitivity.py·structure.py 실행 결과
출력: validation_esif/out/ai_diagnosis.json, ai_diagnosis.md

검증 장치
- 근거 묶음(evidence)에 없는 숫자를 응답이 쓰면 unverified_numbers 로 찾아 표시 (프로젝트 AI 해설과 같은 규칙)
- 각 원인은 '근거 있음(data)'과 '가설(확인 필요)'을 구분해서 받는다
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
sys.path.insert(0, str(ROOT / "backend"))


class Cause(BaseModel):
    rank: int
    title: str = Field(description="원인 한 줄 (한국어)")
    affected: list[Literal["PUE", "냉각탑계", "펌프", "공조 팬", "칠러 시간", "WUE"]]
    direction: str = Field(description="시뮬레이션이 실측보다 큰지 작은지, 어떤 조건에서")
    evidence: str = Field(description="근거 묶음의 숫자를 그대로 인용해 설명")
    basis: Literal["data", "hypothesis"] = Field(description="data=근거 묶음으로 확인됨, hypothesis=추가 확인 필요")
    how_to_verify: str
    model_fix: str = Field(description="엔진·시나리오에서 고칠 점 (수식 변경이면 그렇다고 명시)")


class Diagnosis(BaseModel):
    summary: str = Field(description="3문장 이내 요약")
    causes: list[Cause]
    not_explained: str = Field(description="근거 묶음만으로는 설명할 수 없는 오차와 필요한 추가 데이터")


SYSTEM = """너는 데이터센터 냉각 설비 시뮬레이션 검증 엔지니어다.
주어진 근거 묶음(JSON)만 사용해 '시뮬레이션과 실측의 오차 원인'을 영향이 큰 순서로 정리한다.
규칙:
1) 새로운 계산을 하지 말고, 근거 묶음에 있는 숫자만 그대로 인용한다. 단위를 붙인다.
2) 근거 묶음으로 확인되는 원인은 basis=data, 시설 운영에 대한 추정은 basis=hypothesis 로 표시한다.
3) 근거 묶음에 없는 시설 정보(장비 대수, 제어 로직 등)를 사실처럼 쓰지 않는다. 필요하면 가설로 쓴다.
4) 한국어로 쓴다."""


def evidence():
    load = lambda n: json.loads((OUT / n).read_text(encoding="utf-8"))
    s = load("summary.json")
    import pandas as pd
    mA = pd.read_csv(OUT / "monthly_A_default.csv").round(2)
    return {
        "facility_facts_and_assumptions": {"facts": s["facts"], "assumed": s["assumed"]},
        "weather_used": s["weather"] + (" (Golden, CO 표준기상년 — 실측 연도 기상 아님)" if s["weather"] == "tmy3" else ""),
        "metric_mapping": {"cooling_kw": "냉각탑·건식냉각기 팬 + 동결방지 열선 + 필터 펌프 2.67 kW 고정 ↔ 시뮬 tower_fan",
                           "pump_kw": "ERW·냉각탑수 루프 펌프 + 팬월 부스터 펌프 ↔ 시뮬 chw/cw/liquid 펌프",
                           "hvac_kw": "팬월 + 전기실 팬코일 + 외기 공급기 ↔ 시뮬 crah_fan",
                           "chiller": "실제 ESIF 는 칠러 없음(0 h)"},
        "runs": s["runs"],
        "monthly_A_default": mA.to_dict(orient="records"),
        "chiller_hours_sensitivity": load("sensitivity.json"),
        "structure_test_2019": load("structure.json"),
        "nrel_reference": {"wue_first_year_l_per_kwh": 0.7, "wue_cooling_towers_alone_l_per_kwh": 1.42,
                           "heat_rejection_share": {"cooling_towers": 0.47, "thermosyphon": 0.42, "building_heat": 0.11}},
    }


def to_md(d: Diagnosis, bad: list[str], model: str) -> str:
    lines = [f"# ESIF 오차 원인 진단 (OpenAI {model})", "", d.summary, ""]
    for c in sorted(d.causes, key=lambda x: x.rank):
        lines += [f"## {c.rank}. {c.title}  `{c.basis}`", f"- 영향: {', '.join(c.affected)} / {c.direction}",
                  f"- 근거: {c.evidence}", f"- 확인 방법: {c.how_to_verify}", f"- 모델 수정: {c.model_fix}", ""]
    lines += ["## 설명되지 않은 오차", d.not_explained, ""]
    lines += ["## 숫자 검증", "근거 묶음에 없는 숫자: " + (", ".join(bad) if bad else "없음")]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ev = evidence()
    user = "근거 묶음:\n" + json.dumps(ev, ensure_ascii=False)
    (OUT / "ai_prompt.txt").write_text(SYSTEM + "\n\n" + user, encoding="utf-8")
    if a.dry_run:
        print("프롬프트 저장:", OUT / "ai_prompt.txt", f"({len(user):,}자)")
        return
    from api.ai import OpenAILLM, ai_config
    from hvac.insights import unverified_numbers
    key, model = ai_config()
    if not key or not model:
        sys.exit("backend/.env 에 OPENAI_API_KEY, OPENAI_MODEL 을 설정하세요")
    d = OpenAILLM(key, model).parse(SYSTEM, user, Diagnosis)
    text = " ".join([d.summary, d.not_explained] + [c.evidence + " " + c.direction for c in d.causes])
    bad = unverified_numbers(text, ev)
    (OUT / "ai_diagnosis.json").write_text(json.dumps({"model": model, "unverified_numbers": bad, **d.model_dump()},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "ai_diagnosis.md").write_text(to_md(d, bad, model), encoding="utf-8")
    print(to_md(d, bad, model))


if __name__ == "__main__":
    main()

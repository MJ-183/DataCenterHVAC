"""esif_approx.dxf 를 프로젝트 CAD 가져오기(hvac.cad)로 읽어 ESIF 다중 존 시나리오를 만든다 (도면 ↔ 시나리오 일치 확인)
사용: python cad/import_esif.py → backend/data/scenario_esif_mz.json, cad/esif_import_report.json"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend")); sys.path.insert(0, str(ROOT / "validation_esif"))
from hvac import cad
from hvac.schemas import CoolingLayout, Scenario
from esif_compare import base_scenario, load_measured, load_weather, day_weather
import datetime
import pandas as pd

meta = json.loads((ROOT / "cad/esif_approx_facts.json").read_text(encoding="utf-8"))
p = cad.load((ROOT / "cad/esif_approx.dxf").read_bytes(), "esif_approx.dxf")
ins = cad.inspect(p)
roles = {l["name"]: l["suggested_role"] for l in ins["layers"]}
blocks = {b["name"]: b["suggested_role"] for b in ins["blocks"]} if "blocks" in ins else {}
sc = base_scenario()
DAY = "2017-07-15"     # 보정 기간 중 여름날 (TMY3 기상, 실측 IT)
sc["site"]["date"] = DAY
tmy, site = load_weather()
sc["weather"], _ = day_weather(datetime.date.fromisoformat(DAY), tmy, site)
it = load_measured()["it_power_kw"].reindex(pd.date_range(DAY, periods=24, freq="h")).interpolate(limit_direction="both")
DESIGN = meta["geometry"]["it_design_kw"]
sc["it_load"] = {"design_kw": DESIGN, "utilization": [round(v / DESIGN, 4) for v in it]}
sc = Scenario.model_validate(sc).to_engine_dict()
lay = CoolingLayout.model_validate(sc.get("cooling_layout") or {}).model_dump()
fh = {i: h for i, h in enumerate(meta["geometry"]["floor_heights_m"])}
over = {"pump_1f": {"indoor": {"temp_c": 30.0, "rh_pct": 50.0}, "misc_kw": 3.0, "lighting_w_m2": 0.0},
        "mech_2f": {"indoor": {"temp_c": 30.0, "rh_pct": 50.0}, "misc_kw": 8.0, "lighting_w_m2": 0.0},
        "mech_4f": {"indoor": {"temp_c": 30.0, "rh_pct": 50.0}, "misc_kw": 2.0, "lighting_w_m2": 0.0}}
b = cad.build(p, roles, blocks, sc, lay, floor_heights_m=fh)
sc2 = cad.apply_to_scenario(sc, lay, b)
# 도면에 없는 존 값 (화면에서 존별로 입력하는 것과 같음): 조명·콘센트 실측 3.7 kW 는 전산실에 몰아서, 지원 존 발열은 가정
over["dc_3f"] = {"lighting_w_m2": 4.0, "outdoor_air_m3_h": 1000.0, "people": 0.0}
for z in sc2["zones"]:
    z.update(over.get(z["id"], {}))
    if z["kind"] == "support":
        z.update(people=0.0, outdoor_air_m3_h=0.0)
# 랙별 냉각 방식 (Phase 15 혼합 냉각): Peregrine 노드 랙 = 액체 0.95 (NREL: 95% 이상), 공랭 랙 = 0
for z in sc2["zones"]:
    for it in ((z.get("racks") or {}).get("items") or []):
        it["liquid_fraction"] = 0.95 if (it.get("design_kw") or 0) > 10 else 0.0
# 공기측: 열복도 완전 차폐, 팬월 급기 20.6~23.3 ℃ → 22 ℃ (NREL fy17osti 66690). 코일 접근 1 K 는 가정
sc2["cooling_layout"].update(containment="hot_aisle", supply_temp_c=22.0, coil_approach_k=1.0)
sc2 = Scenario.model_validate(sc2).to_engine_dict()
its = [it for z in sc2["zones"] for it in ((z.get("racks") or {}).get("items") or [])]
phi = sum(it["design_kw"] * it["liquid_fraction"] for it in its) / sum(it["design_kw"] for it in its)
sc2["cooling_plant"]["liquid_fraction"] = round(phi, 3)            # 발열 가중 평균 (비교 시나리오 0.794 와 같음)
for z in sc2["zones"]:
    if z["kind"] == "data_hall":
        z.update(architecture="hybrid_dlc", liquid_fraction=round(phi, 3))
sc2 = Scenario.model_validate(sc2).to_engine_dict()
sc2["_note"] = ("NREL ESIF HPC 데이터센터 근사 (공개 자료 + 가정, cad/esif_approx.dxf 를 CAD 가져오기로 변환). "
                "실제 설계 도면 아님. 조건은 validation_esif 비교와 같음")
sc2["site"]["name"] = "NREL ESIF HPC DC 근사 (다중 존)"
(ROOT / "backend/data/scenario_esif_mz.json").write_text(json.dumps(sc2, ensure_ascii=False, indent=2), encoding="utf-8")
rep = {"effective_liquid_fraction": round(phi, 4), "layers": {k: v for k, v in roles.items() if v and v != "ignore"}, "blocks": blocks,
       "zones": [{k: z.get(k) for k in ("id", "kind", "floor", "floor_area_m2", "it_design_kw")} | {"geometry": z["geometry"],
                  "n_racks": len(((z.get("racks") or {}).get("items")) or [])} for z in sc2["zones"]],
       "it_design_kw": sc2["it_load"]["design_kw"], "plant": sc2["cooling_layout"]["plant"],
       "notes": b.get("report", {}).get("notes"), "warnings": b.get("report", {}).get("warnings")}
(ROOT / "cad/esif_import_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(rep, ensure_ascii=False, indent=1)[:5000])

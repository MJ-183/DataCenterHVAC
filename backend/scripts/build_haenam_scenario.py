"""
해남 솔라시도 AI 데이터센터 (1단계 40 MW) 시나리오 생성 — data/scenario_haenam_ai.json + IT 데이터셋.

backend 폴더에서:  .venv/Scripts/python.exe scripts/build_haenam_scenario.py
입력 파일(바뀌면 다시 실행):
  ../data_itload/it_load_2025_hourly_40MW_haenam.csv   (실측 통계 기반 합성 더미, 연평균 가동률 0.78, 설계 40,000 kW)
  data/weather/annual_kma_261_2025_auto165.json         (기상청 ASOS 해남 261 관측 2025, 일사 목포 165 대용)

공개 보도로 확인되는 값만 출처를 달고(REF), 나머지는 모두 가정값(provenance source='assumed' + note 에 근거).
"""
import csv
import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hvac import DATA_DIR, datasets, itdata  # noqa: E402
from hvac import weather as wx  # noqa: E402
from hvac.airflow import RHO_CP  # noqa: E402
from hvac.schemas import CoolingLayout, Scenario  # noqa: E402

REF = "https://v.daum.net/v/ta3CDHXSW2"   # 공개 보도 (2026-08-03 착공)
CSV = ROOT.parent / "data_itload" / "it_load_2025_hourly_40MW_haenam.csv"
WEATHER = "annual_kma_261_2025_auto165.json"
OUT = DATA_DIR / "scenario_haenam_ai.json"
OUT_MZ = DATA_DIR / "scenario_haenam_ai_mz.json"

# ---- 보도값 ----
GFA_M2 = 16978.0          # 연면적
FLOORS = 2                # 지상 2층
DESIGN_KW = 40000.0       # 1단계 IT 40 MW
# ---- 가정값 (근거는 note) ----
ASPECT_EW_NS = 2.0        # 바닥 가로(동서):세로(남북)
FLOOR_HEIGHT_M = 6.0      # 층고 (고밀도 AI 홀, 액체 배관·덕트 공간)
RACK_KW = 100.0           # 랙당 설계 kW
CRAH_UNIT_MAX_M3_S = 15.0  # CRAH(팬월) 1대 최대 풍량 [m3/s] ≈ 54,000 m3/h
CHILLER_UNIT_KW = 3517.0  # 1,000 RT 원심 칠러
CDU_UNIT_KW = 2000.0      # 대형 CDU
WINDOWS = {"N": 30.0, "S": 30.0, "E": 10.0, "W": 10.0}   # 홀 외피에 창 거의 없음
SITE_LAT, SITE_LON = 34.70, 126.43   # 해남군 산이면 솔라시도 근사 (지도에서 읽은 값, ±0.05°)


def dataset_from_csv(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = row["timestamp"]
            recs.append((date.fromisoformat(ts[:10]), float(ts[11:13]), None, float(row["it_power_kw"])))
    ds = itdata.dataset(recs, DESIGN_KW)
    ds_id = hashlib.sha1(path.read_bytes()).hexdigest()[:12]   # 같은 CSV → 같은 id
    ds["meta"].update(id=ds_id, kind="it", name=path.name, created="2026-11-02T00:00:00",
                      note="실측 통계(코르도바대 DC) 기반 합성 더미 — 설계 40,000 kW, 연평균 가동률 0.78 (규모·모양 가정)")
    p = datasets.ROOT / "it" / f"{ds_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    return ds


def grid(n_racks, ew, ns, g):
    """바닥 안에 들어가는 열 수 중 랙 수를 나누어떨어지게 하는 가장 큰 값 (airflow.rack_positions 와 같은 여유)"""
    max_rows = int((ns - 10) // (g["depth_m"] + g["aisle_m"]))
    max_per = int((ew - 14) // g["width_m"])
    for rows in range(min(max_rows, 60), 0, -1):
        if n_racks % rows == 0 and n_racks // rows <= min(max_per, 200):
            return rows, n_racks // rows
    raise ValueError("랙이 바닥에 들어가지 않습니다")


def build():
    ann = wx.load_annual(wx.WEATHER_DIR / WEATHER)
    days = [{"weather": d["weather"], "meta": {"date": d["date"]}} for d in ann["days"]]
    pick = wx.design_days(days)["hottest_wet_bulb"]
    day = next(d for d in ann["days"] if d["date"] == pick["date"])
    ds = dataset_from_csv(CSV)
    util = [round(v / DESIGN_KW, 5) for v in ds["daily_total_kw"][pick["date"]]]

    floor = GFA_M2 / FLOORS
    ns = math.sqrt(floor / ASPECT_EW_NS)
    ew = floor / ns
    h = FLOOR_HEIGHT_M * FLOORS
    side = {"N": ew, "S": ew, "E": ns, "W": ns}
    sample = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    cp = {**sample["cooling_plant"], "architecture": "hybrid_dlc"}

    lay = CoolingLayout().model_dump(exclude_none=True)
    n_racks = round(DESIGN_KW / RACK_KW)
    rows, per = grid(n_racks, ew, ns, lay["racks"])
    air_design = DESIGN_KW * (1 - cp["liquid_fraction"]) / (RHO_CP * lay["server_delta_t_k"]) * lay["crah"]["design_margin"]
    per_side = math.ceil(air_design / CRAH_UNIT_MAX_M3_S / 2)
    tower_unit = round(CHILLER_UNIT_KW * (1 + 1 / cp["chiller_cop_rated"]), -2)
    lay["racks"].update(rows=rows, per_row=per)
    lay["crah"]["per_side"] = per_side
    lay["containment"] = "hot_aisle"
    lay["plant"].update(chiller_unit_kw=CHILLER_UNIT_KW, tower_unit_kw=tower_unit, cdu_unit_kw=CDU_UNIT_KW, standby_units=1)

    sc = {
        "_note": ("해남 솔라시도 국가AI컴퓨팅센터 1단계 40 MW (공개 보도: 연면적 16,978 m2 지상 2층, 2028 가동 목표). "
                  "냉각 방식·설비 계수·외피·랙 배치는 공개 자료에 없어 모두 가정값. 위경도는 산이면 근사값. "
                  "기류 모델은 단일 층이라 2개 층의 랙 400개를 한 층 평면에 배치한 것으로 근사. "
                  f"기상 = 기상청 ASOS 해남(261) 2025 최고 습구온도일 {pick['date']} (일사 목포 165)."),
        "site": {"name": "해남 솔라시도 AI DC (1단계 40 MW)", "latitude": SITE_LAT, "longitude": SITE_LON,
                 "std_meridian": 135.0, "date": pick["date"], "asos_station_id": 261, "asos_solar_station_id": 165,
                 "asos_solar": "auto", "weather_year": 2025},
        "weather": day["weather"],
        "indoor": sample["indoor"],
        "envelope": {
            "walls": [{"orientation": o, "area_m2": round(side[o] * h - WINDOWS[o], 1), "u_value": 0.35, "absorptance": 0.7}
                      for o in "NESW"],
            "roof": {"area_m2": round(floor, 1), "u_value": 0.25, "absorptance": 0.7},
            "windows": [{"orientation": o, "area_m2": WINDOWS[o], "u_value": 1.8, "shgc": 0.4} for o in "NESW"],
        },
        "it_load": {"design_kw": DESIGN_KW, "utilization": util, "dataset_id": ds["meta"]["id"]},
        "internal": {"floor_area_m2": GFA_M2, "lighting_w_m2": 8, "people": 20, "people_kw_each": 0.13,
                     "ups_loss_ratio": 0.05},
        "ventilation": {"outdoor_air_m3_h": round(GFA_M2, -3)},
        "cooling_plant": cp,
        "cooling_layout": lay,
    }
    at = "2026-11-02T00:00:00"
    prov = {
        "it_load.design_kw": ("user", REF, "1단계 IT 40 MW (공개 보도)"),
        "internal.floor_area_m2": ("user", REF, "연면적 16,978 m2 (공개 보도)"),
        "weather": ("weather_api", f"kma_asos:261:{pick['date']}", "2025 최고 습구온도일 (일사 목포 165)"),
        "it_load.utilization": ("assumed", CSV.name, "합성 더미 IT 부하의 같은 날짜 24시간 / 40,000 kW (실측 통계 기반)"),
        "site.latitude": ("assumed", None, "해남군 산이면 솔라시도 근사 위치 (±0.05°)"),
        "site.longitude": ("assumed", None, "해남군 산이면 솔라시도 근사 위치 (±0.05°)"),
        "envelope": ("assumed", None, f"층당 {floor:,.0f} m2 직사각형 (가로:세로 {ASPECT_EW_NS:g}:1 가정) x 층고 {FLOOR_HEIGHT_M:g} m x "
                                      f"{FLOORS}층, 창 {sum(WINDOWS.values()):g} m2 (가정), U값·흡수율은 서울 샘플과 같음"),
        "internal.people": ("assumed", None, "상주 인원 20명 (가정)"),
        "ventilation.outdoor_air_m3_h": ("assumed", None, "외기 도입 1 m3/h·m2 x 연면적 (서울 샘플과 같은 비율, 가정)"),
        "cooling_plant.architecture": ("assumed", None, "냉각 방식 공개 자료 없음 → GPU 고밀도라 hybrid_dlc 가정 (액체 회수 0.75)"),
        "cooling_layout.racks": ("assumed", None, f"랙당 {RACK_KW:g} kW 가정 → {n_racks}랙, {rows}열 x {per}개 (바닥 치수 안 자동 산정)"),
        "cooling_layout.crah.per_side": ("assumed", None, f"공기측 설계 풍량 {air_design:,.0f} m3/s ÷ 1대 {CRAH_UNIT_MAX_M3_S:g} m3/s (가정) → 남·북 각 {per_side}대"),
        "cooling_layout.containment": ("assumed", None, "고밀도 홀 → 열복도 차폐 가정"),
        "cooling_layout.plant": ("assumed", None, f"칠러 1대 {CHILLER_UNIT_KW:,.0f} kW(1,000 RT), 냉각탑 1대 {tower_unit:,.0f} kW"
                                                  f"(칠러 방열량), CDU 1대 {CDU_UNIT_KW:,.0f} kW, 예비 N+1 — 모두 가정"),
    }
    sc["provenance"] = {k: {"source": s, "at": at, **({"ref": r} if r else {}), "note": n} for k, (s, r, n) in prov.items()}
    sc = Scenario.model_validate(sc).to_engine_dict()
    OUT.write_text(json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{OUT.name}: 설계일 {pick['date']} (습구 최고 {pick['max_wet_bulb_c']}℃), 바닥 {ew:.1f} x {ns:.1f} m, 높이 {h} m, "
          f"랙 {rows}x{per}, CRAH {per_side}x2, 데이터셋 {ds['meta']['id']}")
    return sc


# ---- 다중 존 (2층 적층) 가정값 ----
HALL_EW_M = 95.0          # 층마다 서쪽 95 m 전산실, 동쪽 나머지(약 35 m) 지원 공간
SLAB_U = 2.0              # 층간 콘크리트 슬래브 열관류율 [W/m2.K]
WALL_U = 1.5              # 내벽 열관류율 [W/m2.K]
SUPPORT_TEMP_C = 30.0     # 전기실 실내 설정온도
SUPPORT_MISC_KW = 200.0   # 전기실 장비 발열 (배전반·변압기 등, 냉방부하만)


def build_mz(sc):
    """단일 존 해남 시나리오 → 2층 x (전산실 + 지원 공간) 4개 존. 건물 공통 값(기상·설비·IT 모양)은 그대로"""
    import copy
    floor = GFA_M2 / FLOORS
    ns = math.sqrt(floor / ASPECT_EW_NS)
    ew = floor / ns
    h = FLOOR_HEIGHT_M
    side_ew = ew - HALL_EW_M
    x_hall, x_side = -ew / 2 + HALL_EW_M / 2, ew / 2 - side_ew / 2
    wall = lambda o, a, win=0.0: {"orientation": o, "area_m2": round(a - win, 1), "u_value": 0.35, "absorptance": 0.7}
    win = lambda o, a: {"orientation": o, "area_m2": a, "u_value": 1.8, "shgc": 0.4}

    def env(width, west, east, top, windows=()):
        walls = [wall("N", width * h), wall("S", width * h)]
        if west:
            walls.append(wall("W", ns * h))
        if east:
            walls.append(wall("E", ns * h))
        wins = [win(o, a) for o, a in windows]
        for w in wins:   # 창 면적만큼 벽에서 뺌
            for x in walls:
                if x["orientation"] == w["orientation"]:
                    x["area_m2"] = round(x["area_m2"] - w["area_m2"], 1)
        return {"walls": walls, "roof": {"area_m2": round(width * ns, 1) if top else 0.0, "u_value": 0.25, "absorptance": 0.7},
                "windows": wins}

    lay = sc["cooling_layout"]
    g = lay["racks"]
    n_hall = round(DESIGN_KW / FLOORS / RACK_KW)
    rows, per = grid(n_hall, HALL_EW_M, ns, g)
    racks = {**g, "rows": rows, "per_row": per}
    hall_kw = DESIGN_KW / FLOORS
    air_design = hall_kw * (1 - sc["cooling_plant"]["liquid_fraction"]) / (RHO_CP * lay["server_delta_t_k"]) * lay["crah"]["design_margin"]
    per_side = math.ceil(air_design / CRAH_UNIT_MAX_M3_S / 2)
    geo = lambda width, x: {"ew_m": round(width, 2), "ns_m": round(ns, 2), "height_m": h, "x_m": round(x, 2), "z_m": 0.0}
    slab = lambda other, width: {"zone": other, "area_m2": round(width * ns, 1), "u_value": SLAB_U, "kind": "slab"}
    inner = lambda other: {"zone": other, "area_m2": round(ns * h, 1), "u_value": WALL_U, "kind": "wall"}
    zones = [
        {"id": "hall_1f", "name": "1층 전산실 A", "floor": 0, "kind": "data_hall", "geometry": geo(HALL_EW_M, x_hall),
         "envelope": env(HALL_EW_M, True, False, False), "it_design_kw": hall_kw, "floor_area_m2": round(HALL_EW_M * ns, 1),
         "outdoor_air_m3_h": 5000.0, "racks": racks,
         "partitions": [slab("hall_2f", HALL_EW_M), inner("elec_1f")]},
        {"id": "elec_1f", "name": "1층 전기실", "floor": 0, "kind": "support", "geometry": geo(side_ew, x_side),
         "envelope": env(side_ew, False, True, False), "floor_area_m2": round(side_ew * ns, 1),
         "indoor": {"temp_c": SUPPORT_TEMP_C, "rh_pct": 50.0}, "misc_kw": SUPPORT_MISC_KW, "outdoor_air_m3_h": 2000.0,
         "partitions": [slab("office_2f", side_ew)]},
        {"id": "hall_2f", "name": "2층 전산실 B", "floor": 1, "kind": "data_hall", "geometry": geo(HALL_EW_M, x_hall),
         "envelope": env(HALL_EW_M, True, False, True), "it_design_kw": hall_kw, "floor_area_m2": round(HALL_EW_M * ns, 1),
         "outdoor_air_m3_h": 5000.0, "racks": racks,
         "partitions": [inner("office_2f")]},
        {"id": "office_2f", "name": "2층 사무·관제", "floor": 1, "kind": "office", "geometry": geo(side_ew, x_side),
         "envelope": env(side_ew, False, True, True, (("N", 20.0), ("S", 40.0), ("E", 20.0))),
         "floor_area_m2": round(side_ew * ns, 1), "lighting_w_m2": 12.0, "people": 20.0, "outdoor_air_m3_h": 5000.0},
    ]
    mz = copy.deepcopy(sc)
    mz["zones"] = zones
    mz["site"]["name"] = "해남 솔라시도 AI DC (다중 존 2층)"
    mz["cooling_layout"]["crah"]["per_side"] = per_side
    mz["cooling_layout"]["racks"] = racks
    mz["_note"] = (sc["_note"].split(" 기류 모델은")[0] + f" 다중 존 예시: 층마다 서쪽 {HALL_EW_M:g} m 전산실(20 MW, 랙 {n_hall}개) + 동쪽 "
                   f"{side_ew:.1f} m 지원 공간 (1층 전기실 {SUPPORT_TEMP_C:g}℃·장비 발열 {SUPPORT_MISC_KW:g} kW, 2층 사무·관제). "
                   f"층간 슬래브 U {SLAB_U:g}, 내벽 U {WALL_U:g} W/m2K, 1층 바닥 단열 — 모두 가정값. 기상 = 기상청 ASOS 해남(261) "
                   f"2025 최고 습구온도일 {sc['site']['date']}.")
    at = "2026-11-03T00:00:00"
    mz["provenance"]["zones"] = {"source": "assumed", "at": at, "note": (
        f"존 구성 가정: 층당 전산실 {HALL_EW_M:g} m + 지원 {side_ew:.1f} m, 층고 {h:g} m, 전기실 {SUPPORT_TEMP_C:g}℃·발열 "
        f"{SUPPORT_MISC_KW:g} kW, 사무 조명 12 W/m2·20명, 슬래브 U {SLAB_U:g}·내벽 U {WALL_U:g}, 외기 존별 배분")}
    mz["provenance"]["cooling_layout.crah.per_side"] = {"source": "assumed", "at": at, "note": (
        f"전산실 1곳 공기측 설계 풍량 {air_design:,.0f} m3/s ÷ 1대 {CRAH_UNIT_MAX_M3_S:g} m3/s → 남·북 각 {per_side}대 (두 전산실 공통)")}
    mz = Scenario.model_validate(mz).to_engine_dict()
    OUT_MZ.write_text(json.dumps(mz, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{OUT_MZ.name}: 존 {len(zones)}개, 전산실 랙 {rows}x{per}, CRAH {per_side}x2 (전산실마다)")
    return mz


if __name__ == "__main__":
    build_mz(build())

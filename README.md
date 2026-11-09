# 데이터센터 HVAC 시뮬레이터

하루(24시간) 단위로 데이터센터의 냉방부하·냉수·냉각수·보급수를 계산하고, 시간 슬라이더로 3D 시각화와 2D 대시보드를 함께 보여줍니다.

> ⚠️ `backend/data/scenario_sample.json` 의 기상값은 서울 여름날을 가정한 **가상 데이터**이며 실측값이 아닙니다.
> 외피·설비·내부발열 값도 샘플용 **가정값**입니다.

## 폴더 구조

```
backend/
  hvac/engine.py          계산 엔진 simulate() — 모든 계산은 여기서
  hvac/schemas.py         입력/출력 pydantic 스키마 (프론트 타입의 기준)
  hvac/scenario_patch.py  자연어 시나리오 변경분 적용·검증
  hvac/insights.py        AI 해설용 요약 지표 계산, 해설 속 수치 대조
  hvac/validation.py      BMS CSV 파싱, 오차 지표
  hvac/airflow.py         전산실 기류 저차원 모델 (랙별 흡입온도, 재순환·바이패스, CRAH 팬 전력)
  hvac/piping.py          냉수·냉각수 배관 수력 (관경 선정, 구간 유량, 루프 해석, 펌프 양정)
  hvac/plant.py           기류+배관+칠러 COP 연성 평가 (24시간 전력·에너지, 제약 확인)
  hvac/optimize.py        냉각 구조·운전점 최적화 (격자 전수 탐색)
  hvac/cooling_schemas.py /cooling/* 요청·응답 스키마
  hvac/weather.py         기상 수집·변환 (Open-Meteo / 기상청 ASOS / EPW → weather 24시간값), 설계일 선정, CLI
  hvac/annual.py          연간 시뮬레이션 (365일 x 냉각 방식 3종, 월별 집계·피크일·프리쿨링 히트맵)
  hvac/cad.py             CAD(DXF) 도면 → 외피 면적·건물 치수·랙/CRAH/설비 배치
  hvac/cad_sample.py      샘플 DXF·짝이 맞는 IT 계측 CSV 생성 (가상 데이터)
  hvac/itdata.py          IT 부하 데이터 자동 컬럼 매칭 → 랙별 24시간 프로파일
  hvac/tabular.py         표 파일 공통 파서 (IT·BMS 공용, CSV/엑셀·인코딩·날짜/시각 해석)
  hvac/provenance.py      값 출처 기록·되돌리기·데이터 신뢰도·시작 가이드 상태
  hvac/datasets.py        IT·BMS 기간 데이터셋 저장소 (data/datasets/)
  hvac/plant_vec.py       연성 모델 벡터화 (일 x 시 numpy, plant.energy 와 같은 식)
  hvac/itprofile.py       IT 데이터셋 → 날짜별 24시간 (대체 순서), 내일 IT 예측 (요일·추세)
  hvac/bms.py             BMS 기간 데이터 열 자동 매칭·단위 환산
  hvac/calibrate.py       BMS 실측 보정 (단계별 유계 Levenberg–Marquardt, ASHRAE G14 판정)
  hvac/annual_opt.py      연간 기준 최적화 (구조 고정, 월별 운전점, 변경 폭 제한 동적계획법)
  hvac/outlook.py         내일 운전 예측 (예보 + IT 예측 → 브리핑·권장 운전점·주의)
  hvac/mixed.py           랙별 혼합 냉각 ↔ 엔진 냉각 방식 동기화
  hvac/placement.py       증설 랙 자리 추천 (탐욕 배치 + 이동 개선)
  hvac/invest.py          투자 검토 (개선안·패키지 현금흐름, NPV·IRR·회수기간, 예시 단가)
  api/imports.py          /cad/*, /itload/*
  api/main.py             FastAPI 앱 (/health, /scenario/sample, /simulate)
  api/ai.py               /ai/status, /explain, /scenario/parse
  api/store.py            /scenarios (저장·불러오기·삭제), /bms/template, /bms/compare
  api/cooling.py          /cooling/layout/default, /cooling/analyze, /cooling/evaluate, /cooling/optimize, /cooling/apply
  api/weather.py          /weather/sources, /weather/fetch, /weather/epw, /weather/design-days
  api/annual.py           /annual/weather-files, /annual/run (결과 캐시), /annual/schedule/apply, 작업 annual_optimize
  api/platform.py         /jobs/* (오래 걸리는 계산), /datasets/*, /scenario/status, /scenario/revert
  api/calibration.py      /bms/inspect, /bms/dataset, /calibration/apply, 작업 calibrate
  api/outlook.py          /outlook, /outlook/apply
  api/racks.py            /cooling/racks/apply, 작업 placement
  api/invest.py           /invest/catalog, 작업 invest
  data/                   샘플 시나리오, 회귀 기준(result_sample.json), scenarios/ (저장소), weather/ (수집한 기상 JSON),
                          datasets/ (IT·BMS 기간 데이터셋)
  tests/                  pytest (tests/snapshots/ = 회귀 스냅샷)
frontend/
  src/types/api.ts        backend/hvac/schemas.py, cooling_schemas.py 와 1:1 대응 타입
  src/App.tsx             화면 5개: 데이터 · 현황 · 연간 · 예측 · 개선 + 시작 가이드·데이터 신뢰도·되돌리기·시나리오 메뉴
  src/lib/ui.tsx          공통 UI (작업 진행률·취소, 화면 이동 후 유지되는 상태, 접기, 요약 문장, 예시 데이터 안내)
  src/components/         TimeSlider, Dashboard/, Scene3D/, Cooling/, Import/(CAD·IT), Calibration/(BMS 보정),
                          Annual/(연간), Outlook/(내일 운전), Improve/(연간 최적화·랙·투자)
  src/components/Scene3D/ building(외피·대지) · room(이중마루·랙·컨테인먼트·CRAH) · plant(칠러·냉각탑·펌프·배관)
                          · airflow(기류 입자) · textures(절차적 텍스처)
  src/data/layout.default.json   3D 표시 설정 (형상 크기, GLB 연결). 배치·개수는 시나리오 cooling_layout
```

## 냉각 구조·최적화 (기류·배관 연성 모델)

시나리오의 선택 필드 `cooling_layout` 에 전산실 배치(랙 열·밀도, CRAH 대수·풍량), 컨테인먼트(개방/콜드아일/핫아일),
급기온도, 냉수 배관 방식(단일 헤더/남·북 2계통/루프)을 둡니다. 없으면 기본값이며, **부하 엔진(`/simulate`) 결과에는 영향이 없습니다.**

- `POST /cooling/evaluate` : 랙별 24시간 흡입온도, 배관 구간별 관경·유량, 칠러·CRAH 팬·냉수/냉각수 펌프의 시간별 전력.
  3D 화면의 랙 색·기류 입자·배관 굵기는 모두 이 결과를 그대로 그립니다.
- `POST /cooling/optimize` : 일 냉방 전력량(칠러+팬+펌프) 최소화. 제약은 피크 IT 시각 기준
  랙 흡입온도 ≤ 상한, 급기 ≥ 냉수 공급 + 코일 접근온도, 냉수 환수 ≤ CRAH 환기 − 접근온도.
  컨테인먼트·CRAH 대수·풍량 배율·타일 밸런싱·냉수 ΔT·배관 방식을 격자 전수 탐색하고, 급기온도는 흡입 상한에서 역산합니다.
  CRAH 대수를 바꾸는 후보는 1대 정격 풍량(`crah.unit_airflow_m3_s`)을 현재 장비로 고정해 대수만큼 총풍량·팬 전력이 달라지고,
  배관도 대수별로 다시 해석합니다. CRAH 설계 풍량은 공기로 나가는 발열(IT x (1 − 액체 회수율)) 기준이라 DLC·액침도 후보가 나옵니다.
- `POST /cooling/apply` : 후보를 시나리오(냉수 온도 + `cooling_layout`)에 반영. 화면의 '적용' 버튼이 사용.

모델 한계: 기류는 CFD가 아닌 집중정수 모델이고, 컨테인먼트별 재순환/바이패스 비율·코일 접근온도·팬/펌프 효율·
냉수 온도에 따른 COP 보정률은 **가정값**입니다. 공사비·냉각탑 팬 전력·습도 제어는 목적함수에 없습니다.
실측 보정 전에는 대안 간 상대 비교용으로 사용하세요.

## 냉각 방식 · 프리쿨링 · PUE/WUE (Phase 3.5)

`cooling_plant.architecture` 로 냉각 방식을 고릅니다. 기본값 `air_chw` 와 샘플 시나리오의 기존 출력값은 바뀌지 않습니다(회귀 테스트).

| 방식 | 열 경로 |
|---|---|
| `air_chw` | 전 발열 → CRAH(공기) → 냉수 → 칠러 → 냉각수 → 냉각탑 |
| `hybrid_dlc` | IT × `liquid_fraction` → 냉각판 → TCS → CDU → 건물측 냉각수(FWS) → 냉각탑(+보조 칠러), 나머지 → 공랭 경로 |
| `immersion` | IT × `immersion_capture` → 액침 냉각액 → CDU → FWS → 냉각탑(+보조 칠러), 나머지 → 공랭 경로 |

- FWS 공급/환수 = TCS 공급/환수 − `cdu_approach_k`. 액체로 회수된 열은 CRAH 를 거치지 않으므로 CRAH 팬 발열도 공기측 부하에만 비례.
- 프리쿨링(`free_cooling`): 프리쿨링 수온 = 외기 습구 + `tower_approach_k` + `hx_approach_k`. 루프별로 공급온도 이하면 칠러 OFF(`free`),
  공급~환수 사이면 환수 예냉(`partial`, 비율 = (환수 − 수온)/(환수 − 공급)), 그 외 `chiller`. 시간별 `plant.mode` 와 루프별 비율을 출력.
- 칠러 COP = 정격 × 습구 보정 × 공급수온 보정(`cop_supply_sensitivity`, 기준 `cop_ref_supply_c`). 액체 루프 보조 칠러는 FWS 공급온도로 보정.
- 보조동력: 냉수·냉각수·TCS/FWS 펌프, 냉각탑 팬 = 처리 열량 × kW/kW 계수.
  PUE = (IT + UPS 손실 + 조명 + 냉방 설비)/IT, WUE = 보급수[L] / IT 전력량[kWh]. 일 합계에 프리쿨링·부분 프리쿨링 시간 포함.
- `POST /simulate/compare` : 같은 날씨·IT 부하에서 세 방식을 계산해 일 합계·시간별 모드·PUE 비교 (화면 '냉각 방식 비교' 탭).
- 신규 계수는 모두 **가정값**(`hvac/engine.py` 의 `PLANT_DEFAULTS`, 화면에 '가정값' 표시). 액침의 서버 팬 제거에 따른 IT 전력 감소,
  변압기 등 기타 손실, 부분부하 효율은 미반영.
- 냉각 구조 탭(기류·배관)도 방식을 따릅니다: 랙 요구 풍량은 공기로 나가는 발열만, 연성 평가에 액체 루프 전력(`liquid`)과 공기측 프리쿨링 포함.

## CAD·IT 데이터 가져오기 → 예측·최적화 (Phase 8)

화면 'CAD·IT 가져오기' 탭: ① 도면 적용 → ② IT 데이터 적용 → '부하 예측·증설' / '냉각 구조·최적화' 탭.
샘플: `backend/data/cad/sample_dc.dxf`, `backend/data/itload/sample_it_hourly.csv` (**가상 데이터**, `python -m hvac.cad_sample` 로 재생성).

**CAD (DXF 만)** — DWG 는 AutoCAD 에서 '다른 이름으로 저장 → DXF' 후 업로드.
- `POST /cad/inspect` : 레이어·블록 목록, 이름 키워드로 역할 추정(외곽선/외벽/창/랙/CRAH/칠러/냉각탑/무시), 미리보기. 블록 역할이 레이어보다 우선
- `POST /cad/build` : 외곽선(outline → wall 의 가장 큰 닫힌 폴리선 → wall 선 외접 사각형 순) → 방위별 벽 = 변 길이 × 층고 − 창,
  지붕·바닥 = 외곽선 면적. 랙(블록 또는 닫힌 사각형) → 좌표·열·id(블록 속성 > 랙 안 문자 > 자동 번호)·설계 kW(속성). CRAH·칠러·냉각탑 좌표
- 결과는 시나리오 `building`(건물 치수) 과 `cooling_layout.racks.items`(개별 랙) 로 저장. 없으면 기존 방식(외피 역산, 격자 배치) 그대로
- 가정: 진북 각도 = 도면 +Y 에서 시계방향, 벽 방위는 외향 법선의 가장 가까운 N/E/S/W, 창 면적 = 도면 폭 × 창 높이(입력),
  3D·기류 좌표계는 랙 열이 x 축과 평행하게 회전, 랙 전면 방향은 짝수 열 남·홀수 열 북 규칙, 직교하는 랙 열·호(bulge)·중첩 블록 내부는 미지원

**IT 부하 (컬럼 자동 매칭)** — CSV/TSV/엑셀(.xlsx, 시트별), 인코딩 utf-8·cp949 자동.
- 헤더 행 자동 탐지(제목 줄 건너뜀), 열 역할 = 헤더 동의어(한/영) + 값 모양 점수: 일시 / 날짜+시각 / 시(0~23, 1~24) / 랙·PDU id /
  전력(W·kW·MW) / 누적 전력량(kWh → 차분) / 가동률(% × 설계 kW) / 설계 kW / 냉각 방식
- 표 형태: 세로형(시각·랙·값), 가로형(헤더가 랙 id), 합계, 랙 목록. 화면에서 열 역할·형태·단위를 바꾸면 그 표만 재해석
- 하루 선택: 설계일(IT 합계 최대인 날) / 시각별 평균 / 날짜 지정. 1시간 평균, 결측은 순환 선형 보간
- 랙 id 매칭: 대소문자·기호·앞자리 0·RACK/R 접두어 무시 (`rack-a01` = `A01`). 짝 없는 부하는 짝 없는 랙에 설계 kW 비례(없으면 균등) 배분
- 반영: `it_load.design_kw`(랙 목록 합계 > 도면 속성 합계 > 측정 최대), `utilization`, 랙별 `kw_profile`(기류 모델 랙 발열 배분)
- 단위 표기가 없으면 값 크기로 W/kW 추정(가정, 화면에 근거 표시). 전류(A) 열은 kW 로 바꾸지 않음. 랙별 냉각 방식 혼합은 아직 미반영

## 연간 시뮬레이션 — 계절별 냉방부하 (Phase 9)
- 기상 수집: `python -m hvac.weather open-meteo --year 2025 --lat 37.57 --lon 126.98` → `data/weather/annual_37.57_126.98_2025.json`
  (Open-Meteo 재분석 격자값, 관측소 실측 아님. 끝난 연도만). 서울 2025년 파일이 포함돼 있음
- `hvac/annual.py`: 365일 각각 `simulate()` (그날 24시간 기상, IT 가동률 = 시나리오 24시간 모양 x 일별 배율) x 냉각 방식 3종.
  일별 배율 = 기본값 x (1 + 연간 증설률 x 경과일/연간일수), 설계부하 고정 — 가정값
- 출력: 일별·월별·연간 합계(냉방부하, 칠러·CRAH 팬·펌프·냉각탑 팬 전력, PUE, WUE, 프리쿨링/부분/칠러 시간, 보급수),
  월별 피크일, 월 x 시 프리쿨링 비율, 방식별 연간 비교, 가정 notes
- API: `GET /annual/weather-files`, `POST /annual/run` (시나리오 + `weather_file` 또는 `latitude/longitude/year`, 없으면 받아서 저장). 결과는 메모리 LRU 캐시
- 화면 '연간' 탭: 월별 냉방 전력(구성 적층)·보급수 막대, 방식별 월 PUE, 일별 냉방 전력, 프리쿨링 히트맵, 월별 표.
  막대나 '24시간 보기'를 누르면 그 달 피크일(그날 기상·배율·선택한 방식)을 위 24시간 화면으로 엶
- 성능: 365일 x 3방식 약 1.3초 (60분 간격). 기존 `/simulate` 결과는 변하지 않음 (회귀 테스트 유지)

## 기상 데이터 수집 (Phase 5)

| 소스 | 키 | 성격 | 비고 |
|---|---|---|---|
| `open_meteo` | 불필요 | 재분석 격자값(ERA5/IFS 등) — 관측소 실측 아님 | 위경도로 조회. CC BY 4.0, 화면에 "Weather data by Open-Meteo.com" 표기 |
| `kma_asos` | `KMA_SERVICE_KEY` | 기상청 종관(ASOS) 관측소 실측 | 공공데이터포털 "기상청_지상(종관, ASOS) 시간자료 조회서비스" 활용신청. 전일(D-1)까지 제공. 서울 지점 108 |
| `epw` | 불필요 | EnergyPlus 기상파일(보통 TMY 표준기상년) | 파일 업로드. 특정 연도 실측 아님 |

```powershell
cd backend; .\.venv\Scripts\Activate.ps1
# 하루치 → data/weather/*.json 저장, 샘플 시나리오에 반영한 새 시나리오도 저장
python -m hvac.weather open-meteo --date 2025-08-05 --lat 37.57 --lon 126.98 --scenario data\scenario_sample.json
python -m hvac.weather kma --stn 108 --date 2025-08-05            # .env 에 KMA_SERVICE_KEY 필요
python -m hvac.weather epw --epw C:\path\seoul.epw --date 08-05
# 기간 조회 → 최고 건구일·최고 습구일·습구 중앙값일 3개 저장 (냉방 설계일 후보)
python -m hvac.weather open-meteo --start 2025-06-01 --end 2025-09-30
```

시간 정렬(가정): 엔진은 h:00 순간값을 쓰므로 기온·습도는 h:00 값을 그대로, 일사는 소스가 주는 '직전 1시간 평균'을
앞뒤 두 구간 평균으로 바꿔 씁니다. 기상청 `icsr` 는 MJ/m²(1시간 누적)로 보고 W/m² 로 환산합니다(포털 문서에 단위 미표기 → 가정,
최대 1400 W/m² 초과 시 오류). 해가 진 구간의 빈 일사값은 0, 기온·습도는 연속 3시간 이하 결측만 선형 보간하고 그 이상은 오류로 멈춥니다.
적용한 보간·가정은 결과 `meta.notes` 에 기록됩니다.

## 설치 (Windows, PowerShell)

```powershell
# 백엔드
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # 실행 정책 오류 시: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt

# 프론트엔드 (Node 20+)
cd ..\frontend
npm install
```

## 실행 (터미널 2개)

```powershell
# 터미널 1 - 백엔드
cd backend; .\.venv\Scripts\Activate.ps1
uvicorn api.main:app --reload            # http://127.0.0.1:8000/docs

# 터미널 2 - 프론트엔드
cd frontend
npm run dev                               # http://localhost:5173
```

- 8000 포트가 이미 사용 중이면: `uvicorn api.main:app --reload --port 8001` 로 띄우고
  프론트는 `$env:VITE_API_BASE="http://127.0.0.1:8001"; npm run dev`
- 프론트를 5173 이외 포트로 띄우면 백엔드에 `$env:HVAC_CORS_ORIGINS="http://localhost:5174"` 지정
- `http://localhost:5173/?hour=17` 처럼 시작 시각 지정 가능
- 화면은 5분 간격 연속 계산(`POST /simulate?step_min=5`)을 사용. `step_min` 기본값은 60(기존 1시간 결과와 동일).
  60 미만이면 시간별 입력(기상·IT 가동률)을 정시 사이 선형 보간(가정, 23시 이후는 0시로 순환), 태양 위치는 분 단위 계산,
  일 합계 = Σ(값 × Δt). 값은 60의 약수(1, 5, 10, 15, 30 …)만 허용.
- 코드 수정이 반영되지 않으면 서버를 재시작하세요 (이 PC에서 파일 감시가 일부 변경을 놓친 적이 있음).

## Phase 10~16 — 화면 개편 · 연간 최적화 · 내일 예측 · BMS 보정 · 혼합 냉각·배치 · 투자 검토

설계서: [docs/SDD_phase10-16.md](docs/SDD_phase10-16.md). 화면은 5개로 줄였습니다.

| 화면 | 내용 |
|---|---|
| 데이터 입력 | ① 도면(DXF) ② IT 데이터 (기간 전체를 데이터셋으로 저장, 달력·요일 프로파일) ③ BMS 실측 보정 (선택) |
| 운전 현황 | 24시간 3D·KPI·차트, 냉각 구조 평가 요약 4개(주의·경고 해결 방법 포함), AI 요약, 입력값·냉각 구조 직접 수정(접기) |
| 연간 예측 | 365일 x 냉각 방식 3종, IT 소스(데이터셋/시나리오), 연성 모델 연간 전력, 월 피크일 열기 |
| 브리핑 | 내일·모레 운전 브리핑 (Open-Meteo 예보, 권장 운전점, 주의 알림, 곡선 해석) |
| 최적화 | 운전 최적화(연간 기준 월별 스케줄 / 하루 기준), 자동 튜닝(주의·경고를 우선순위 파라미터로 해결), 랙별 냉각 방식·증설 배치, 투자 검토, 최종 보고서(연간 예측·연간 최적화 완료 후 A4 인쇄·PDF 저장) |

- 상단: 시작 가이드(도면 → IT → BMS 보정 → 연간 → 개선안 → 투자), 되돌리기, 시나리오 저장·불러오기
- IT 증설 한계·CRAH N+1 검증은 제외했습니다 (화면·API·계산 모듈 모두 삭제)
- 예시 데이터(샘플 시나리오, 투자 예시 단가·요금)로 계산 중이면 "예시 데이터를 이용해 시연 계산한 결과" 안내를 표시
- 2초 넘는 계산(연간 최적화·보정·배치·투자)은 작업(Job)으로 돌려 진행률·취소를 제공, 화면을 옮겨도 결과 유지
- 모든 "적용"은 provenance 에 직전 값을 남겨 되돌릴 수 있음. 보정은 모델 계수만, 최적화는 설계·운전 변수만 바꿈 (서로 덮어쓰지 않음)
- 새 스키마 필드는 모두 선택이며 기본값이면 기존 결과와 같음 (tests/test_snapshots.py)
- 제외한 것(사용자 결정): 계절·시간대 전기요금표, 공휴일 구분, 예보 불확실성 범위, 화면 자동 테스트.
  자연어 시나리오·시나리오 비교·하루 BMS 비교·냉각 방식 비교 탭은 화면에서 뺐습니다 (백엔드 API 는 남아 있음)
- 주의·경고(흡입 상한 초과·여유 부족, 코일 접근온도, 랙 혼합 불일치, 증설 후 흡입 상승)는 원인 설명과 해결 방법을 함께 보여 줍니다
  (`hvac/advice.py`). 해결 방법마다 기류 모델로 다시 계산한 예상 결과와 '설정 바로가기'가 있고, 바로가기는 권장값을 입력칸에 채워 두기만 합니다.

## AI 요약 (현황 화면) / 자연어 시나리오 (API 만)

`backend/.env` 의 `OPENAI_API_KEY`, `OPENAI_MODEL` 을 채우고 저장하면 됩니다 (매 요청마다 다시 읽어 서버 재시작 불필요).
키는 백엔드에서만 읽으며 프론트로 전달되지 않습니다. (`/explain`, `/scenario/parse`)

- 해설: 서버가 피크·비중·최소/최대를 코드로 계산해 전달하고, 응답 속 모든 숫자를 결과값과 자동 대조해 불일치 수치를 표시합니다.
- 자연어 시나리오: 모델은 `경로 + 연산(set/multiply/add) + 값` 만 제안하고, 산술·범위 검증은 서버, 부하 계산은 엔진이 합니다.
  변경될 필드를 표로 확인한 뒤 적용합니다.

## 실측 BMS 비교 (하루, API)

`/bms/template` 의 CSV 양식을 받아 `hour`(0~23)와 실측값 열(`total_cooling_kw, chw_flow_m3_h, cw_flow_m3_h, chiller_power_kw, chiller_cop, makeup_m3_h` 중 일부)을 채워 업로드합니다.
MAE, RMSE, NMBE, CV(RMSE)를 표시하며, 참고 판정값은 ASHRAE Guideline 14 시간 단위 기준(|NMBE| ≤ 10%, CV(RMSE) ≤ 30%)입니다.

## 테스트

```powershell
cd backend; python -m pytest       # 엔진 손계산·회귀, API, AI(가짜 LLM), 저장소, BMS, 기류·배관·최적화
cd frontend; npm run typecheck
```

## 엔진 CLI

```powershell
cd backend
python -m hvac.engine data\scenario_sample.json result.json
```

## Phase 진행 상황

| Phase | 상태 |
|---|---|
| 1 계산 엔진 | 완료 |
| 2 API 서버 | 완료 |
| 3 2D 대시보드 | 완료 |
| 4 3D 시각화 | 완료 (엔진 출력에 태양 고도·방위각, 면별 입사 일사량 추가 — 기존 수치 불변) |
| 4.5 AI 해설 | 완료 (실제 OpenAI 호출은 키가 있는 환경에서 확인 필요) |
| 5 실제 데이터 | 기상 수집 완료(`hvac/weather.py`, `/weather/*`, 실제 API 호출은 이 PC에서 확인 필요). 3D는 구조만 대비: `layout.default.json` 의 `model`, `zones` |
| 6 시나리오·검증 | 완료 (저장/비교, BMS CSV 비교) |
| 7 냉각 구조·최적화 | 완료 (랙 단위 기류 모델, 배관 수력, 칠러 COP 연성, 격자 최적화, 사실적 3D 전산실·설비) |
| 3.5 냉각 방식 확장 | 완료 (air_chw / hybrid_dlc / immersion, 프리쿨링 모드, PUE·WUE, 방식별 비교 뷰 — 계수는 가정값) |
| 9 연간 시뮬레이션 | 완료 (Open-Meteo 1년 기상, 365일 x 3방식 집계, 연간 탭·피크일 열기) |
| 10~16 | 완료 — 화면 개편, IT 데이터셋, BMS 보정, 연간 최적화, 내일 예측, 혼합 냉각·배치, 투자 검토 ([설계서](docs/SDD_phase10-16.md)) |
| 17 해남 40 MW 시나리오 | 완료 — 상단 시나리오 선택(해남 AI DC 40 MW / 서울 샘플(회귀용)), 설비 대수 산정 연결, CRAH 상한 120 ([결과](docs/haenam_40mw_results.md)) |
| 18 다중 존 · 자동 튜닝 | 완료 — 적층 건물 존별 계산·합산(엔진 수식 불변, 단일 존 패리티 테스트), 전산실별 기류·배관 합산으로 예측·최적화·투자 동작, 예시 '해남 AI DC 다중 존 (2층 적층)', 자동 튜닝(우선순위 단계 탐색), CAD 다중 층·다중 존 가져오기(층 접두어·존 경계 → zones, 3층 더미 도면 정답 대조), 건물 전체 3D(층·존·랙·칠러/냉각탑/CDU N대·입상관 배관, 건물 좌표) ([설명](docs/phase18_multizone_autotune.md)) |
| 19 실측 비교 | 완료 — 상단 '실측 비교' 탭. 장기 시간 실측(ESIF 프리셋 또는 CSV)과 같은 조건 시뮬 비교: A 기본 계수 / B 보정 기간 계수 비례 보정 / C 검증 기간 적용, 항목별 NMBE·CV(G14, 부호 = 시뮬 − 실측)·상관·월별, 항목 × 실행 소분할 그래프, A → B 보정 단계 0~5, 구조 검증(고정분 회귀)·칠러 민감도(고도 습구, 분석 전용), OpenAI 오차 원인 진단(숫자 대조), B 계수 시나리오 적용(출처 real_calibrated). 엔진 수식 불변 (`hvac/realdata.py`, `hvac/real_compare.py`, `/real/*`, 프로토타입 [validation_esif](validation_esif/README.md), [화면](docs/screenshots/phase19_real_compare.png)) |
| 8 CAD·IT 데이터 | 완료 (DXF 외피·배치, IT 데이터 자동 컬럼 매칭, 증설 한계·N+1 예측). 남은 일: 기상 예보 기반 운영 예측, 랙별 냉각 방식 혼합, 직교 랙 열 |

Phase 5에서 남은 일: 프론트 기상 불러오기 화면, 칠러 성능 곡선, 프리쿨링, 부분부하 효율, 다중 존 엔진.

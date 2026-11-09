# 데이터센터 HVAC 시뮬레이터 — Phase 10~16 구현 계획 · 소프트웨어 설계서(SDD)

- 작성일: 2026-10-29
- 기준 코드: Phase 9 완료 시점 (백엔드 테스트 150개 통과)
- 범위: 다음 6개 기능을 서로 충돌 없이, 사용자 친화성을 우선해 구현하기 위한 계획과 설계
  1. 연간 기준 최적화 (계절별 운전 전략)
  2. 기상 예보 기반 내일 운전 예측
  3. IT 데이터 여러 날 사용 (요일·계절 패턴)
  4. BMS 실측 보정
  5. 랙별 냉각 방식 혼합 + 증설 랙 배치 최적화
  6. 투자비·회수기간

---

## 0. 한눈에 보기

| Phase | 기능 | 핵심 산출물 | 앞선 Phase |
|---|---|---|---|
| **10** | 공통 기반 | 단계형 화면 구조·시작 가이드, 데이터 출처(provenance), 작업(Job) API, 데이터셋 저장소, 표 파서 분리, 벡터화 연성 에너지 모델 | – |
| **11** | ③ IT 여러 날 | IT 데이터셋(기간 전체 저장), 월·요일별 대표 프로파일, 연간 계산의 IT 소스 선택 | 10 |
| **12** | ④ BMS 보정 | 여러 날 BMS 자동 매칭, 단계별 매개변수 보정, 검증(ASHRAE G14), 적용·되돌리기 | 10, (11) |
| **13** | ① 연간 최적화 | 월별 운전 스케줄(변경 폭 제한), 연성 모델 연간 에너지, 스케줄 적용 | 10, (11, 12) |
| **14** | ② 내일 예측 | Open-Meteo 예보 + IT 추세 → 내일 브리핑·권장 운전점·위험 알림 | 10, 11, 13 |
| **15** | ⑤ 혼합·배치 | 랙별 공랭/DLC 지정, 혼합 냉각 기류, 증설 랙 자리 추천 | 10, (13) |
| **16** | ⑥ 투자 검토 | 요금·단가 입력, 개선안·패키지별 NPV·회수기간·탄소, 민감도 | 10, 13, 15 |

**구현 순서를 1→6이 아니라 위 순서로 정한 이유**
- ③(IT 여러 날)과 ④(보정)는 다른 모든 예측의 **입력 품질**을 올립니다. 먼저 해 두면 ①·②·⑥ 결과를 다시 계산할 일이 줄어듭니다.
- ②(내일 예측)의 권장 운전점은 ①의 운전점 탐색 엔진을 그대로 씁니다.
- ⑥(투자)은 ①(운전 최적화)과 ⑤(배치·DLC 전환)의 결과를 "개선안"으로 받아 비용을 붙이는 마지막 단계입니다.
- 괄호 안의 Phase는 없어도 동작하지만, 있으면 결과가 좋아지는 선택적 의존입니다. 순서를 바꿔도 깨지지 않게 설계했습니다(§2.2).

---

## 1. 설계 원칙

### 1.1 사용자 친화 원칙 (모든 Phase 공통, 최우선)

| # | 원칙 | 구체 규칙 |
|---|---|---|
| U1 | **다음에 할 일이 보인다** | 상단 "시작 가이드"에서 단계마다 완료/선택/미완료를 표시하고, 다음 단계 버튼을 하나만 강조 |
| U2 | **결과는 문장 먼저, 숫자는 그 다음** | 모든 결과 카드 맨 위에 1~3줄 요약 문장 (예: "7~8월에 칠러 전력이 연간의 41%입니다. 월별 운전으로 연 312 MWh(−9.8%)를 줄일 수 있습니다") |
| U3 | **가정값이 숨지 않는다** | 값마다 출처 배지(측정·CAD·IT·보정·최적화·가정). 결과 화면에 "이 결과의 가정 n개" 접이식 목록 |
| U4 | **기본값으로 바로 돌아간다** | 모든 입력은 기본값이 있고, 고급 설정은 "고급" 접기 안에 둠. 처음 쓰는 사용자도 버튼 하나로 결과를 볼 수 있어야 함 |
| U5 | **오래 걸리면 진행률과 취소** | 2초 넘는 계산은 Job으로 돌려 진행률·단계 메시지·취소 버튼 제공. 화면 이동해도 계속 진행 |
| U6 | **되돌릴 수 있다** | 시나리오를 바꾸는 "적용"은 모두 되돌리기(직전 값 복원) 가능. 적용 전에 바뀌는 값을 미리 보여줌 |
| U7 | **오류는 원인과 해결책** | "무엇이 → 왜 → 어떻게 고치면 되는지" 3요소. 예: "IT 데이터 날짜가 기상 연도(2025)와 겹치지 않아 월·요일 패턴으로 대신 계산했습니다" |
| U8 | **같은 개념은 같은 모양** | 냉각 방식 색·운전 모드 색·단위 표기·숫자 자릿수를 공통 모듈(lib/fields.ts, lib/format.ts)에서만 정의 |

### 1.2 무충돌 원칙

| # | 원칙 | 구체 규칙 |
|---|---|---|
| C1 | **추가만, 기본값이면 결과 동일** | 새 스키마 필드는 모두 선택(optional)이며, 기본값일 때 기존 결과와 소수점까지 같아야 함. 기존 저장 시나리오는 그대로 열림 |
| C2 | **필드마다 쓰는 기능을 정해 둔다** | §2.3의 "쓰기 소유권" 표에 없는 기능은 그 필드를 바꾸지 않음 |
| C3 | **우선순위 사슬은 하나** | 같은 값을 여러 소스가 줄 때 §2.2의 순서만 따름 (예: 랙 발열 = kw_profile > design_kw > row_density > 균등) |
| C4 | **기존 API는 바꾸지 않고 늘린다** | 기존 엔드포인트의 요청·응답 형식은 유지하고, 새 기능은 새 엔드포인트나 선택 필드로 추가 |
| C5 | **모델이 둘임을 명시** | 엔진 모델(engine.simulate, 팬 발열 비율)과 연성 모델(plant.energy, 기류 기반 팬·배관)을 섞지 않음. 화면에 어느 모델 결과인지 항상 표시 (§2.4) |
| C6 | **공유 파일은 구역별 추가** | schemas.py, types/api.ts, client.ts, main.py는 Phase별 구역 주석 아래에만 추가. 화면 탭은 App.tsx가 아니라 nav 레지스트리(§3.1)에 등록 |
| C7 | **회귀 스냅샷** | Phase 10에서 기준 결과를 스냅샷으로 고정. 이후 Phase가 스냅샷을 바꾸려면 테스트에서 명시적으로 갱신하고 README에 기록 |
| C8 | **스칼라 = 벡터** | 벡터화 연성 모델은 기존 스칼라 plant.energy와 결과가 같아야 함 (상대오차 1e-6 이내, 패리티 테스트) |

---

## 2. 데이터·모델 규칙 (충돌 방지의 핵심)

### 2.1 현재 구조 요약 (변경 기준선)

```
엔진 모델  engine.simulate(sc)            단일 존 24h: 부하 → 냉수·냉각수·보급수·칠러(+액체 루프)·PUE
연성 모델  plant.energy / evaluate        엔진 결과 위에 기류(airflow.analyze) 팬 전력 + 배관(piping) 펌프 + 칠러 COP 재계산
최적화     optimize.optimize              하루 기준 격자 탐색 (컨테인먼트·CRAH·풍량·타일·ΔT·배관), 급기온도 역산
연간       annual.run                     365일 x 3방식 엔진 모델 집계
예측       forecast.growth / redundancy   IT 배율별 한계, CRAH N+1
가져오기   cad.build / itdata.build       DXF → 외피·배치, IT 표 → 랙별 24h 프로파일
```

### 2.2 값 우선순위 사슬 (단일 진실 공급원)

| 값 | 24시간 화면 | 연간 / 내일 예측 | 우선순위 (앞이 이김) |
|---|---|---|---|
| 기상 | `scenario.weather` | 연간 파일 / 예보 API | 보정용: BMS 외기 열 > Open-Meteo 과거값 |
| IT 합계 24h | `it_load.utilization` x `design_kw` | `it_source` 선택: `dataset` 대표 프로파일 > `scenario` 모양 x 일별 배율 | 같은 날짜 실측 > 월·요일 대표값 > 시나리오 모양 |
| 랙별 배분 | 기존 규칙 | 같음 | `kw_profile` > `design_kw` > `row_density` > 균등 |
| 액체 회수율 | 랙 φᵢ (Phase 15) > 냉각 방식 기본 | 같음 | 랙 φᵢ가 하나라도 있으면 엔진 전역값 = 가중평균으로 동기화 (§7.4) |
| 운전점 (급기·냉수·풍량) | `cooling_layout` / `cooling_plant` 현재값 | 월 스케줄(Phase 13)이 있으면 그 달 값, 없으면 현재값 | 내일 권장값은 **표시만**, 적용은 사용자가 결정 |
| 모델 계수 (COP, 접근온도, 팬·펌프) | 시나리오 필드 | 같음 | 보정값도 **같은 필드**에 쓰고 출처는 provenance에 기록 |

### 2.3 필드 쓰기 소유권

| 필드 그룹 | 쓰는 기능 | 쓰지 않는 기능 |
|---|---|---|
| 외피·건물·랙 위치·CRAH 위치 | CAD 적용, 사용자 편집, 배치 최적화 적용(새 랙 추가만) | 보정, 연간 최적화, 투자 |
| `it_load.*`, 랙 `kw_profile` / `design_kw` | IT 적용, 사용자 편집, 배치 적용(새 랙 design_kw) | 보정, 최적화 |
| `it_load.dataset_id` | IT 데이터셋 저장 (Phase 11) | 그 외 전부 |
| 설계 변수 (컨테인먼트, CRAH 대수·1대 정격, 배관 방식) | 하루·연간 최적화 적용, 사용자 편집, 투자안 적용 | 보정 |
| 운전점 (급기, 냉수 공급/환수, 풍량 배율, 타일) | 하루·연간 최적화 적용, 스케줄 적용, 사용자 편집 | 보정 |
| `cooling_layout.operating_schedule` | 연간 최적화 적용, 사용자 편집 | 그 외 전부 |
| 모델 계수 (COP·민감도·접근온도·팬·펌프 효율·재순환 배율·기타 발열·COC) | 보정 적용, 사용자 편집 | 최적화, 투자 |
| 랙 `liquid_fraction` | 랙 편집(Phase 15), 투자안 "DLC 전환" 적용 | 보정, 최적화 |
| `economics` | 투자 화면 | 그 외 전부 |
| `provenance` | 위 모든 "적용" 동작이 자기 필드에 대해서만 | – |

→ **보정은 계수만, 최적화는 설계·운전 변수만** 쓰므로 순서와 관계없이 서로 덮어쓰지 않습니다.

### 2.4 두 모델의 사용처

| 결과 | 모델 | 화면 표기 |
|---|---|---|
| 24시간 부하·PUE·보급수, 연간 탭(기존) | 엔진 | "엔진 모델 (팬 발열 비율 가정)" |
| 냉각 구조 평가, 하루·연간 최적화, 내일 권장 운전점, 투자 절감액 | 연성 | "연성 모델 (기류·배관 반영)" |
| 연간 탭(Phase 13 이후) | 둘 다 | 연간 탭에 "연성 모델 보기" 토글. 기본값은 기존 엔진 표시 |

연성 모델의 냉각탑 팬 전력·보급수는 엔진 값을 그대로 씁니다(현행 유지). 투자 분석의 물 비용도 엔진 보급수를 씁니다.

### 2.5 데이터 출처 (provenance) — Phase 10

```python
class Provenance(_In):
    source: Literal["assumed", "user", "cad", "it_data", "bms_calibrated", "optimizer", "schedule", "weather_api"]
    ref: str | None = None          # 데이터셋 id, 파일 이름, 보정 실행 id 등
    at: datetime | None = None
    previous: float | str | bool | None = None   # 되돌리기용 직전 값
    note: str | None = None

Scenario.provenance: dict[str, Provenance] | None = None   # 키 = "cooling_plant.chiller_cop_rated" 같은 경로
```

- 키가 없는 필드는 schemas.py의 description에 "(가정값)"이 있으면 `assumed`, 없으면 `user`로 봅니다. 자동 목록은 `hvac/provenance.py: assumed_fields()`가 만듭니다.
- 되돌리기: `POST /scenario/revert {scenario, paths[]}` 가 `previous` 값을 복원하고 해당 키를 지웁니다.
- 화면: 입력 칸 옆 작은 배지와, 결과 카드의 "이 결과의 가정 n개" 목록이 모두 이 정보를 씁니다.

---

## 3. Phase 10 — 공통 기반

### 3.1 화면 구조 개편 (사용자 친화)

탭이 14개를 넘으므로, 2단계 내비게이션과 시작 가이드로 바꿉니다. 기존 컴포넌트는 그대로 두고 배치만 바꿉니다.

```
┌ 상단: 사이트·날짜·방식·PUE ─────────────────────────────── [시나리오 저장] ┐
│ 시작 가이드  ① 도면 ✓  ② IT 데이터 ✓  ③ BMS 보정(선택) ○  ④ 연간 분석 ●  ⑤ 개선안 ○  ⑥ 투자 ○ │
│ 데이터 신뢰도: 측정·보정 기반 62% · 가정 38%  [가정 목록 보기]                       │
├ 24시간 화면 (3D · KPI · 차트) — 항상 표시, 접기 가능 ──────────────────────────────┤
│ [데이터]   CAD·IT 가져오기 | BMS·보정 | 입력값 편집                                 │
│ [분석]     냉각 구조 | 냉각 방식 비교 | 연간                                         │
│ [예측]     내일 운전 | 증설·N+1                                                      │
│ [개선]     운전 최적화(하루·연간) | 랙 배치 | 투자 검토                              │
│ [도구]     시나리오 비교 | AI 해설 | 자연어 시나리오                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

- `frontend/src/nav.ts` 레지스트리: `{ id, group, label, component, badge?(state) }[]`. 각 Phase는 이 파일에 한 줄을 추가하고, App.tsx는 레지스트리만 렌더링합니다(C6).
- 시작 가이드의 상태는 provenance와 시나리오 내용에서 계산합니다.
  - ① 건물이 CAD에서 왔는지(`building.source == "cad"`)
  - ② `kw_profile`이나 `dataset_id`가 있는지
  - ③ `bms_calibrated` 항목이 있는지
  - ④ 연간 결과가 있는지
  - ⑤ 최적화나 스케줄을 적용했는지
  - ⑥ `economics`가 있는지
- 각 단계는 "왜 필요한지" 한 줄과 "건너뛰면 어떤 가정이 쓰이는지"를 보여줍니다(U1, U3).
- 기존 ItDataNotice는 시작 가이드 ② 단계 설명과 같은 문구로 통일합니다.

### 3.2 작업(Job) API — 오래 걸리는 계산

```
POST   /jobs/{kind}     body = 해당 기능 요청        → {job_id}
GET    /jobs/{job_id}   → {status: queued|running|done|error|cancelled, progress 0~1, stage, message, result?, error?}
DELETE /jobs/{job_id}   → 취소 요청 (협조적 취소)
```

- `api/jobs.py`: 동시 실행 2개(ThreadPoolExecutor), 결과는 메모리 LRU 16개, 1시간 뒤 만료.
- 등록 kind: `annual_optimize`(13), `calibrate`(12), `placement`(15), `invest`(16). 연간 실행(9)은 2초 이내라 기존 동기 API를 유지합니다.
- 긴 함수는 모두 `progress: Callable[[float, str], None] | None = None` 인자를 받습니다. 취소는 progress 콜백 안에서 `JobCancelled`를 던지는 방식입니다.
- 기존 동기 엔드포인트는 그대로 두며(C4), 테스트도 동기 함수를 직접 호출합니다.
- 프론트 `hooks/useJob.ts`는 0.5초마다 상태를 조회하고, 진행 막대·단계 문구·취소 버튼을 갖춘 공통 `<JobProgress>`로 표시합니다. 탭을 옮겨도 job_id를 App 상태에 보관해 계속 이어집니다(U5).

### 3.3 데이터셋 저장소

```
backend/data/datasets/it/<id>.json      (Phase 11)
backend/data/datasets/bms/<id>.json     (Phase 12)
backend/data/datasets/index.json        {id, kind, name, created, period, size}
```

- `api/datasets.py`: `GET /datasets?kind=`, `GET /datasets/{id}`(요약), `DELETE /datasets/{id}`
- id는 `uuid4().hex[:12]`이고, 경로 검증은 정규식으로 합니다(기존 store.py와 같은 방식).
- 시나리오에는 id만 저장합니다. 파일이 없으면 경고를 띄우고 시나리오 값으로 대신합니다("데이터셋 abc가 삭제되어 시나리오 24시간 모양을 썼습니다").

### 3.4 표 파서 분리 (`hvac/tabular.py`)

- itdata.py의 범용 부분을 동작 변경 없이 옮깁니다: `read_tables`, `_decode`, `to_num`, `to_time`, `to_date`, `to_datetime`, `norm_header`, `unit_in_header`, `find_header`, `profile_column`, `natural_key`.
- 열 역할 매칭은 `match_columns(table, roles: dict[role, RoleSpec])`로 일반화합니다. RoleSpec = 동의어, 값 형태 판정 함수, 필수 여부입니다.
- itdata.py는 IT 역할 사전으로, bms(Phase 12)는 BMS 역할 사전으로 이 함수를 호출합니다.
- 완료 조건: 기존 test_itdata.py가 수정 없이 통과해야 합니다.

### 3.5 벡터화 연성 에너지 모델 (`hvac/plant_vec.py`)

연간 최적화·예측·투자는 "같은 구조에서 운전점 수백 개 × 365일"을 평가해야 합니다. 스칼라 `plant.energy`(24시간 Python 반복)로는 30~70초가 걸리므로 numpy 배열 연산으로 바꿉니다.

```python
@dataclass
class DayMatrix:                    # 엔진 결과에서 한 번만 추출 (D일 x 24시)
    q_air_engine: np.ndarray        # 엔진 공기 루프 부하
    fan_engine: np.ndarray          # 엔진 CRAH 팬 발열 (교체 대상)
    t_fc: np.ndarray                # 프리쿨링 수온
    wet_bulb: np.ndarray
    liq_kw: np.ndarray              # 액체 루프 칠러+펌프 전력
    liq_heat: np.ndarray
    it_kw: np.ndarray
    dates: list[str]

def day_matrix(sc, days, it_rows) -> DayMatrix                  # 엔진을 D회 실행 (1 ms/일)
def energy_matrix(dm, cp, pc, fan_kw, chw_s, chw_r, nets) -> dict[str, np.ndarray]   # (D,24) 항목별 kW
```

- `fan_kw`, `chw_s`, `chw_r`는 스칼라 또는 (D,) 배열이라, 월별로 다른 운전점을 한 번에 평가할 수 있습니다.
- 패리티 테스트(C8): 무작위 운전점 50개 × 10일에서 `plant.energy`와 상대오차 1e-6 이내여야 합니다.
- 기존 `plant.energy`는 바꾸지 않고, 공통 수식은 `_hour_terms()`로 빼서 두 구현이 공유합니다.
- requirements.txt에 `numpy>=1.26`을 명시합니다(지금은 ezdxf 의존성으로만 설치돼 있음).

### 3.6 최적화 내부 함수 분리 (동작 동일)

- `optimize.py`에서 다음을 꺼내 Phase 13·14·15가 재사용하게 합니다.
  - `structures(cfg, space)`: 설계 변수 조합
  - `feasible_supply(sc, cfg, c, hour, sp)`: 급기온도 역산·제약 판정
  - `nets_for`: 배관 캐시
- 기존 `optimize()` 결과는 스냅샷과 같아야 합니다.

### 3.7 회귀 스냅샷

`tests/snapshots/`:
- `simulate_sample.json`: 기존 result_sample 비교를 확장한 것
- `annual_seoul2025_totals.json`: 방식별 연간 합계
- `optimize_sample_top1.json`: 기본 공간 최적 후보 1개
- `evaluate_sample_summary.json`

### 3.8 완료 기준

- 기존 테스트 150개와 새 테스트가 모두 통과
- 화면 기능은 그대로이고 배치만 바뀜
- 시작 가이드·신뢰도 배지 표시
- 연간 실행을 Job으로 돌려도 결과 동일

---

## 4. Phase 11 — ③ IT 데이터 여러 날 사용

### 4.1 사용자 시나리오

1. 사용자가 IT 계측 파일(예: 3개월치)을 올립니다. 지금처럼 열이 자동 매칭되고, "24시간 화면에 쓸 하루"를 고릅니다.
2. 새 체크박스 **"기간 전체를 데이터셋으로 저장 (연간·내일 예측에 사용)"**는 기본으로 켜져 있습니다.
3. 적용하면 데이터셋 요약 카드가 나옵니다.
   - 기간, 포함된 월, 결측률
   - 일별 IT kWh 달력 히트맵, 평일·토·일 24시간 프로파일
   - 요약 문장 예: "평일 낮 피크가 주말보다 18% 높습니다."
4. 연간 탭의 IT 소스 선택에서 "IT 데이터셋 (월·요일 패턴)"이 기본값이 됩니다.

### 4.2 데이터 모델 (`datasets/it/<id>.json`)

```json
{
  "meta": {"id": "...", "name": "파일 이름들", "created": "...", "period": ["2026-06-01", "2026-08-31"],
           "n_days": 92, "coverage": 0.97, "design_kw": 2160, "racks_matched": 240, "notes": [...]},
  "daily_total_kw": {"2026-06-01": [24 values], ...},
  "rack_ids": ["A01", ...],
  "rack_share_how": [[168 values], ...],
  "typical": {"by_month_daytype": {"6": {"weekday": [24], "sat": [24], "sun": [24]}, ...},
              "by_daytype": {"weekday": [24], "sat": [24], "sun": [24]}}
}
```

- `rack_share_how`: 랙별 요일x시(168칸) 평균 점유율입니다. 합계 프로파일을 랙별로 나눌 때 씁니다.
- 날 유형은 평일/토/일입니다. 공휴일은 선택 입력(`holidays: ["2026-08-15", ...]`)으로 받아 일요일로 취급합니다. 한국 공휴일 기본 목록을 둘지는 §11에서 결정합니다.
- 크기: 랙 240개 × 168칸 + 1년 8,760칸이면 약 1 MB 미만입니다.

### 4.3 백엔드

- `itdata.dataset(analyses, sc, racks, holidays=None) -> dict`
  - 기존 `records()`의 (날짜, 시, 랙, kW)를 그대로 받아 집계하므로 파싱 로직을 중복하지 않습니다.
  - 날짜 정보가 없는 데이터(시각만 있음)는 데이터셋을 만들 수 없습니다. 이 경우 "날짜가 없어 하루 프로파일로만 사용합니다"라고 안내하고 체크박스를 끕니다.
- `hvac/itprofile.py`
  - `day_profile(ds, date, design_kw, growth) -> list[24 utilization]`
  - 대체 순서: 같은 날짜 > 같은 월·날 유형 > 날 유형 전체 > 전체 평균
  - 무엇으로 대체했는지 날짜별로 기록하고, 결과에는 "실측 날짜 12일 · 월·요일 패턴 290일 · 전체 평균 63일" 같은 요약을 냅니다.
- annual.run 확장(C1):
  - `it_source: Literal["scenario", "dataset"] = "scenario"`, `dataset_id: str | None = None`
  - `dataset`이면 일별 utilization을 위 함수로 만들고, 연간 증설률은 그 위에 곱합니다.
- 연도 정렬: 기상 연도와 IT 연도가 다르면 월·날 유형으로 맞춥니다. 같은 연도이면 같은 날짜를 우선합니다.

### 4.4 API

- `POST /itload/apply`에 선택 필드를 추가합니다: `save_dataset: bool = true`, `holidays: list[date] = []`. 응답에 `dataset` 요약을 추가합니다. 기존 필드는 그대로입니다.
- `GET /datasets/{id}`는 요약과 달력용 일별 kWh, 날 유형 프로파일을 줍니다.
- `POST /annual/run`에 `it_source`, `dataset_id`를 선택 필드로 추가합니다.

### 4.5 화면

- ItImport: 저장 체크박스, 공휴일 입력(고급), 적용 후 데이터셋 카드(달력 히트맵·날 유형 라인·커버리지)
- AnnualPanel: "IT 소스" 선택(시나리오 모양 / 데이터셋). 데이터셋 선택 시 대체 요약을 notes와 배지로 표시
- 시작 가이드 ②: "하루 프로파일만" / "기간 데이터셋 있음" 두 단계로 표시

### 4.6 테스트 · 완료 기준

- 합성 데이터(평일 1.0, 주말 0.8, 8월 +5%)
  - 데이터셋 대표 프로파일이 입력 패턴을 1% 이내로 재현
  - 연간 결과의 평일/주말 IT 합계 비율이 0.8로 재현
- 대체 순서 테스트(월 결측 → 날 유형 전체), 날짜 없는 데이터 거부 메시지
- `it_source="scenario"`이면 연간 스냅샷과 같아야 함(C1)
- 성능: 데이터셋 생성 100만 행 기준 5초 이내, 연간 실행 증가분 0.3초 이내

---

## 5. Phase 12 — ④ BMS 실측 보정

### 5.1 사용자 시나리오 (4단계 마법사)

1. **올리기**: BMS 내보내기 파일(CSV/엑셀, 여러 날)을 올립니다. 열이 자동 매칭되며, 매칭 결과 화면은 IT 가져오기와 같은 모양입니다(U8).
2. **확인**: 매칭된 지표와 보정 가능한 계수가 표로 나옵니다.
   - 예: "칠러 전력 열이 있어 칠러 COP를 보정할 수 있습니다", "랙 흡입온도 열이 없어 기류 재순환은 보정하지 않습니다"
   - 외기 열이 없으면 그 날짜의 Open-Meteo 과거값을 자동으로 받는다고 안내합니다.
3. **보정 실행**(Job): 단계별 진행률과 단계 이름(부하 → 칠러 → 프리쿨링 → 팬·펌프 → 기류)을 표시합니다.
4. **결과·적용**
   - 계수별 이전 → 이후 값, 범위 한계에 닿은 계수 경고
   - 지표별 보정 전/후 오차(CV(RMSE), NMBE)와 ASHRAE Guideline 14 판정 배지
   - 검증 기간 시계열 겹쳐 그리기
   - "적용" 또는 "적용 안 함", 적용 후 언제든 "보정 되돌리기"

### 5.2 지표 (자동 매칭 역할)

| 역할 | 단위 추론 | 연결 결과 |
|---|---|---|
| timestamp / date+hour | – | – |
| outdoor_temp_c, outdoor_rh_pct | ℃, % | 기상 대체 (있으면 우선, §2.2) |
| it_kw | W/kW | IT 소스 (IT 데이터셋 > 이 열 > 시나리오, 시나리오면 경고) |
| total_cooling_kw | kW, RT(x3.517) | 엔진 total_cooling |
| chiller_kw, chiller_cop | kW | 엔진 / 연성 칠러 |
| chw_supply_c, chw_return_c, chw_flow_m3_h | ℃, m³/h, L/s | 운전점 확인(보정 대상 아님, 시나리오와 다르면 안내) |
| cw_flow_m3_h, makeup_m3_h | m³/h | 엔진 |
| crah_fan_kw, chw_pump_kw, cw_pump_kw | kW | 연성 |
| crah_return_c, rack_inlet_max_c, rack_inlet_mean_c | ℃ | 기류 |

기존 `/bms/compare`(하루 CSV, hour 열)는 그대로 유지합니다(C4). 새 경로는 여러 날·자동 매칭용입니다.

### 5.3 보정 대상 계수와 단계

| 단계 | 계수 (범위) | 쓰는 지표 | 비고 |
|---|---|---|---|
| S1 부하 | `internal.misc_kw` **신규** (−20%~+20% x IT 설계) | total_cooling_kw | 모델에 없는 발열·누설 보정. 기본 0이면 엔진 결과 동일(C1) |
| S2 칠러 | `chiller_cop_rated` (2~10), `cop_wb_sensitivity` (0~0.06) | chiller_kw, chiller_cop | 칠러 모드 시간만 사용 |
| S3 프리쿨링 | `tower_approach_k` (1.5~8), `hx_approach_k` (0.5~4) | chiller_kw = 0 시간 비율, 부분부하 시간 칠러 kW | 겨울·간절기 데이터가 있을 때만 |
| S4 팬·펌프 | `crah.fan_kw_per_m3_s` (0.1~1.0), `piping.pump_efficiency` (0.4~0.9) | crah_fan_kw, *_pump_kw | 연성 모델 |
| S5 기류 | `cooling_layout.recirculation_scale` **신규** (0.3~2.0) | rack_inlet_*, crah_return_c | CONTAINMENT 재순환 기본비에 곱함. 기본 1.0이면 결과 동일 |
| S6 물 | `tower_cycles_of_concentration` (2~10) | makeup_m3_h | |

- 알고리즘: 단계별로 정규화 가중 최소제곱(잔차 / 실측 평균)을 풉니다. numpy로 직접 구현한 유계 Levenberg–Marquardt(유한차분 야코비안, 계수 ≤ 2개/단계)를 쓰므로 scipy 의존을 추가하지 않습니다. 앞 단계 결과를 고정한 채 다음 단계를 풀어 식별성 문제를 줄입니다.
- 검증: 날짜 기준 앞 70%로 보정하고 뒤 30%로 검증합니다. 14일 미만이면 교차검증 없이 전체로 보정하고 "검증 기간 부족" 배지를 붙입니다.
- 판정: 시간 단위 CV(RMSE) ≤ 30%, |NMBE| ≤ 10%이면 "기준 충족"입니다(ASHRAE G14 시간 기준). 기존 validation.py 지표 함수를 재사용합니다.
- 범위 경계에 닿은 계수는 "데이터나 모델 구조 확인 필요"로 표시하고 적용 시 확인을 받습니다.
- 적용: 해당 필드에 값을 쓰고 provenance에 `{source: "bms_calibrated", ref: run_id, previous}`를 남깁니다. 설계·운전 변수는 건드리지 않습니다(§2.3).

### 5.4 모듈 · API

- `hvac/bms.py`: BMS 역할 사전, `inspect`, `dataset`(tabular 사용)
- `hvac/calibrate.py`: `run(sc, cfg, bms_ds, it_ds=None, weather_days=None, stages=None, progress=None)`
- 엔드포인트
  - `POST /bms/inspect`
  - `POST /bms/dataset`
  - `POST /jobs/calibrate`
  - `POST /calibration/apply` (run_id → 시나리오)
  - `POST /scenario/revert`
- 신규 필드 2개: `Internal.misc_kw: float = 0`, `CoolingLayout.recirculation_scale: PosNum = 1.0`. 엔진·기류 반영은 각 한 줄이며, 기본값일 때 스냅샷과 같아야 합니다.

### 5.5 테스트 · 완료 기준

- 참값 회복: 계수를 알고 있는 시나리오로 BMS를 합성하고 잡음 3%를 더한 뒤 보정하면 참값의 ±5% 이내로 회복
- 열 누락 시 해당 단계가 건너뛰어지고 사유가 표시됨
- 범위 경계 경고가 뜸
- 적용 → 되돌리기 왕복 후 시나리오가 원래와 완전히 같음
- 성능: 30일 × 6단계를 20초 이내(Job)

---

## 6. Phase 13 — ① 연간 기준 최적화 (계절별 운전 전략)

### 6.1 사용자 시나리오

- '운전 최적화' 화면 위쪽에 [하루 기준 | **연간 기준**] 전환을 둡니다.
- 연간 기준에서 고를 것은 기상 파일, IT 소스, 탐색할 구조 후보, "월별 변경 폭 제한"(기본 2℃)입니다.
- 결과 요약 문장 예: "현재 운전 대비 연 312 MWh(−9.8%) 절감. 11~3월은 풍량을 45%로 낮추고(프리쿨링 구간), 6~9월은 냉수 공급을 18.5℃로 올리는 전략입니다."
- 비교 세 가지를 막대로 나란히 보여줍니다: 현재 운전 / 연중 고정 최적 / 월별 최적.
- 월별 스케줄 표(급기·냉수·풍량·예상 kWh·흡입 여유)와 월별 절감 막대를 보여줍니다. 월을 누르면 그 달 피크일을 24시간 화면으로 엽니다(Phase 9 방식).
- "스케줄 적용"을 누르면 구조 변수와 12개월 스케줄을 저장합니다. 미리보기로 바뀌는 값을 보여주고 되돌릴 수 있습니다(U6).

### 6.2 알고리즘

1. **구조 후보**: 현재 구조와, 연중 피크일 기준 하루 최적화 상위 3개 구조(컨테인먼트·CRAH 대수·배관)입니다. 사용자는 체크박스로 고릅니다. 구조를 고정하는 이유는 공사가 필요한 변수를 월마다 바꿀 수 없기 때문입니다.
2. **운전점 격자**: 풍량 배율 0.3~1.5(0.05 간격)와 냉수 ΔT {5,6,7,8,10}으로, 구조당 최대 125개입니다.
3. **월별 제약**: 그 달의 IT 최대 시각(데이터셋이면 월마다 다름)에 기류를 풀어 급기온도를 역산하고 제약(흡입 상한, 코일 접근온도)을 확인합니다. 기류는 기상과 무관하므로 (구조, 풍량, 월 IT 피크)당 한 번만 계산합니다.
4. **에너지**: `plant_vec.energy_matrix`로 (운전점 × 일 × 시)를 한 번에 평가합니다. 구조 4개 × 125 × 365 × 24 ≈ 438만 칸이며 목표는 수 초입니다.
5. **스케줄 선택**: 월 m에서 운전점 k의 비용 E[m,k]에 대해 동적계획법으로 최적 경로를 찾습니다.
   - 목적: `min Σ E[m,k_m]`
   - 제약: |급기(k_m) − 급기(k_{m−1})| ≤ 변경 폭 제한(12월 → 1월 포함)
   - 상태 125개, 12단계라 계산량은 무시할 만합니다. 제한을 끄면 월별 최솟값이 됩니다.
6. **연중 고정 최적**: 12개월 모두에서 가능한 운전점 중 연간 합이 최소인 것입니다.
7. **결과 검증**: 최종 스케줄을 스칼라 `plant.energy`로 다시 계산해 벡터 결과와의 차이가 0.1% 이내인지 확인합니다(C8).

### 6.3 데이터 모델

```python
class ScheduleEntry(_In):
    month: int = Field(ge=1, le=12)
    supply_temp_c: float
    chw_supply_c: float
    chw_return_c: float
    airflow_ratio: float = Field(gt=0, le=3)

CoolingLayout.operating_schedule: list[ScheduleEntry] | None = None   # 12개, 월 중복 금지 검증
```

- 24시간 화면·냉각 구조 평가는 지금처럼 **현재 운전점**을 씁니다. 스케줄을 적용할 때 현재 운전점을 `site.date`가 속한 달의 값으로 맞춥니다.
- 연간 화면에서 피크일을 열면 그 달 스케줄 값을 현재 운전점으로 넣습니다.
- 사용자가 운전점을 직접 고치면 "월별 스케줄 사용 중 — 이 값은 7월 항목에도 반영할까요?" 확인을 띄웁니다. 아니면 현재값만 바뀝니다.
- annual.run은 스케줄이 있으면 월별 냉수 온도를 엔진 일별 시나리오에 넣습니다(엔진의 COP·프리쿨링이 냉수 온도에 의존). 없으면 기존과 같습니다(C1).
- 연간 탭에 "연성 모델 보기" 토글을 추가합니다. `annual.run(..., coupled=True)`가 plant_vec으로 냉각 구조 반영 연간 kWh를 함께 냅니다.

### 6.4 API · 테스트

- 엔드포인트
  - `POST /jobs/annual_optimize` {scenario, weather_file | lat/lon/year, it_source, dataset_id, structures?, max_step_c=2, top_n=3}
  - `POST /cooling/schedule/apply`
- 테스트
  - 겨울이 추운 합성 기상에서 겨울 풍량이 여름보다 낮거나 같음
  - 변경 폭 제한을 지킴
  - 월별 ≤ 고정 ≤ 현재 (에너지 순서)
  - 스칼라 검증 0.1% 이내
  - 스케줄이 없으면 연간 스냅샷과 동일
  - 적용 → 되돌리기 왕복
- 성능: 서울 2025, 구조 4개 기준 10초 이내(Job, 진행률 단계 = 구조별)

---

## 7. Phase 14 — ② 기상 예보 기반 내일 운전 예측

### 7.1 사용자 시나리오

'내일 운전' 화면을 열면 자동으로 계산합니다(캐시 1시간). 맨 위는 **운전 브리핑 카드**입니다.

```
내일 10/30(목) · 서울 · 예보 기준 (Open-Meteo, 불확실성 ±1.5℃ 반영)
• 최대 냉방부하 2,040 kW (14시) — 설비 용량 대비 82%
• 프리쿨링: 01~08시 완전, 09~11시 부분 / 칠러 단독 13시간
• 권장 운전점: 급기 24.0℃, 냉수 18.0/26.0℃, 풍량 60% → 현재 대비 −410 kWh(−6%)
• 주의: 15~16시 랙 흡입온도 여유 0.8℃ (C열 끝 랙) — 풍량 하한 60% 이하 금지
[24시간 화면으로 열기]  [권장 운전점 적용]  [모레 보기]
```

- 아래에 시간별 냉방부하 띠 그래프(예보 하한~상한), 운전 모드 띠, 현재 운전 대비 권장 운전 전력 비교를 둡니다.
- 데이터 상태에 따라 안내 문구가 달라집니다: IT 데이터셋 있음/없음, 보정 여부, 월 스케줄 있음/없음.

### 7.2 설계

- **기상**: `weather.fetch_open_meteo_forecast(lat, lon, days=3, timezone)`
  - `https://api.open-meteo.com/v1/forecast`, 같은 변수(temperature_2m, relative_humidity_2m, shortwave_radiation)
  - 기존 `_parse_open_meteo` + `_day_from_series`를 재사용하므로 시간 정렬 규칙(일사 직전 1시간 평균 → 순간값)이 같습니다.
  - meta.source = `open_meteo_forecast`이고, "예보값 — 실제와 다를 수 있음"을 표기합니다.
  - 시간대: 엔진이 표준시를 쓰므로 `std_meridian/15`에서 고정 오프셋 IANA 이름(`Etc/GMT-9`)을 만들어 요청합니다. 서머타임 지역의 시차 문제를 피하기 위해서이며, 스키마는 바꾸지 않습니다.
  - 서버 캐시는 (위경도, 날짜) 기준 1시간입니다.
- **불확실성** v1: 건구온도 ±Δ(내일 1.0℃, 모레 1.5℃, 가정값)로 하한·상한 두 번 더 계산하고, 상대습도는 고정해 습구가 함께 움직이게 합니다. v2(선택): Open-Meteo 앙상블 API의 p10/p90입니다.
- **IT 예측**: `itprofile.forecast(ds, target_date, lookback_weeks=4)`
  - 최근 4주 같은 요일의 시각별 평균에 추세 계수를 곱합니다. 추세 계수는 최근 28일 일평균의 선형회귀 기울기이며, 주당 ±10%로 제한합니다.
  - 데이터셋이 없으면 시나리오 24시간 모양을 쓰고 "IT 데이터셋이 없어 시나리오 모양을 썼습니다"라고 안내합니다.
- **권장 운전점**: Phase 13 엔진을 하루(D=1)로 실행합니다.
  - 구조는 현재값으로 고정합니다.
  - 제약은 내일 IT 피크 시각, 상한 기상(보수적) 기준입니다.
  - 스케줄이 있으면 "이 달 스케줄 값"과 "내일 최적값"을 함께 보여줍니다.
- **위험 알림 규칙**(설정 가능, 기본값은 가정)

  | 조건 | 표시 |
  |---|---|
  | 흡입 여유 < 1℃ | 주의 |
  | 흡입 여유 < 0 | 위험 |
  | 피크 냉방부하 > 설비 용량의 90% (용량 입력 시) | 주의 |
  | 보급수가 최근 평균의 1.3배 초과 | 정보 |
  | 예보 상한에서만 제약을 넘음 | "불확실성 범위 내 위험" |

- **모듈·API**
  - `hvac/outlook.py: tomorrow(sc, cfg, days=1|2, capacity_kw=None) -> briefing`
  - `POST /outlook`(동기, 2초 이내 목표, 캐시)
  - `POST /outlook/apply`(권장 운전점 → 현재 운전점, provenance `optimizer`)

### 7.3 테스트 · 완료 기준

- 예보 응답 모의값으로 정렬 확인(과거 API 테스트와 같은 규칙)
- 하한 ≤ 기준 ≤ 상한의 단조성
- 권장 운전점이 상한 기상에서도 제약을 만족
- IT 예측: 합성 주간 패턴 + 추세에서 요일·추세 재현
- 네트워크 실패 시 "예보를 받지 못했습니다 — 잠시 후 다시 시도하거나 연간 파일의 같은 날 값으로 대신 볼 수 있습니다" 안내와 함께, 대체 계산 버튼 제공

---

## 8. Phase 15 — ⑤ 랙별 냉각 방식 혼합 + 증설 랙 배치 최적화

### 8.1 사용자 시나리오

- **랙 편집 모드**(냉각 구조 화면)
  - 2D 평면도나 3D에서 랙을 클릭하거나 끌어서 여러 개를 고릅니다.
  - 고른 랙의 냉각 방식(공랭 / DLC 냉각판 φ=0.75 / 사용자 φ)과 설계 kW를 일괄 지정합니다.
  - 열 단위 일괄 지정도 됩니다. 색으로 방식을 표시합니다.
- **배치 추천 마법사**
  1. 추가할 랙 묶음 입력: "DLC 40 kW × 8대, 공랭 12 kW × 20대"
  2. 후보 자리 선택: 격자 빈칸 / CAD "랙 자리(slot)" 레이어 / "빈 바닥에 열 연장" 자동
  3. 제약 선택: 흡입 상한(기본), 열당 최대 kW, CDU 용량(DLC)
  4. 결과
     - 3D에서 반투명 새 랙과 흡입온도 전후 비교 색
     - 요약 문장 예: "DLC 랙 8대는 B·C열 CRAH 근처에 배치해 최대 흡입 23.9℃(여유 3.1℃), 공랭 20대는 E·F열 끝을 피해 배치"
     - "적용"을 누르면 랙 item을 추가

### 8.2 설계

- **스키마**
  - `RackItem.liquid_fraction: Fraction | None = None` (None = 냉각 방식 기본값)
  - `RackGrid.row_liquid_fraction: list[Fraction] | None = None` (격자 배치용)
  - CAD 역할에 `slot`(빈 랙 자리)을 추가합니다. 기존 역할은 바뀌지 않습니다.
- **기류**: `v_req_i = kw_i (1−φ_i)/(ρc_p ΔT)`. 공기측 설계 풍량(`crah_unit_rated`)은 Σ design_kw_i (1−φ_i) 기준입니다. φ_i가 모두 None이면 현재 식과 같습니다(C1).
- **엔진 동기화** (`hvac/mixed.py`)
  - `effective_capture(sc, cfg)` = Σ design_kw_i φ_i / Σ design_kw_i
  - `sync(sc)`: 랙 φ_i가 하나라도 있으면 `architecture="hybrid_dlc"`, `liquid_fraction=effective`로 설정하고 provenance `{source: "user", note: "랙별 혼합에서 자동 계산"}`을 남깁니다.
  - `sync`는 랙 편집 적용, 배치 적용, 투자안 DLC 전환 적용에서만 호출합니다.
  - `/cooling/evaluate`는 불일치(> 0.01)가 있으면 경고를 냅니다.
  - 냉각 방식 비교 탭은 혼합일 때 "현재 (랙별 혼합, 유효 φ 0.41)"로 표시합니다.
- **배치 알고리즘** (결정적, Job)
  1. 후보 자리 집합 S, 추가 랙 목록 N을 kW 내림차순(어려운 랙 먼저)으로 정렬
  2. 탐욕 배치: 랙마다 S의 각 자리에 임시로 넣고 피크 시각 기류를 풉니다. 목적은 사전식입니다: (제약 만족) → (최대 흡입온도 최소) → (연성 일 에너지 최소). 가장 좋은 자리에 확정합니다.
     - 증분 계산: CRAH 커버리지 행렬 `cov[slot]`을 미리 계산하고, 랙 추가 시 배분식만 다시 풉니다.
  3. 국소 개선: 새 랙끼리, 새 랙과 빈 자리 사이 2-opt 교환을 개선이 없을 때까지(최대 200회) 반복
  4. 선택 "기존 고온 랙 재배치 제안": 흡입 상위 5개 랙과 여유 자리 교환 효과만 보고(자동 적용 안 함)
- **성능**: 새 랙 30대 × 후보 300자리 기준 15초 이내(증분 기류, Job 진행률 = 배치된 랙 수)
- **모듈·API**
  - `hvac/placement.py: place(sc, cfg, new_racks, slots, constraints, progress)`
  - `POST /jobs/placement`
  - `POST /cooling/racks/apply` (편집·배치 결과 → 시나리오, `sync` 포함)

### 8.3 테스트 · 완료 기준

- φ_i 전부 None이면 기류·평가 스냅샷과 같음
- 전부 0.75면 hybrid_dlc 전역 결과와 같음
- 혼합 시 유효 φ 가중평균이 맞음
- 배치: 합성 배치에서 고밀도 랙이 CRAH 근처에 놓이고, 제약을 만족하는 해가 있으면 위반 없이 찾음
- 같은 입력이면 같은 결과(결정적)
- 적용 → 되돌리기 왕복

---

## 9. Phase 16 — ⑥ 투자비·회수기간

### 9.1 사용자 시나리오

- '투자 검토' 화면은 **요금·조건 → 개선안 카드 → 결과** 세 구역입니다.
- **요금·조건**
  - 전기요금은 단일 단가 또는 계절·시간대 요금표(편집 가능한 표)
  - 기본요금 ₩/kW·월(선택), 물 ₩/m³(상·하수 합산), 탄소배출계수 kgCO₂/kWh, 할인율, 분석 기간(년), 에너지 단가 상승률, IT 증설률(연간 화면 값 가져오기)
- **개선안 카드**: 켜기/끄기, 투자비 입력. 기본 단가는 "예시값 — 견적으로 바꾸세요" 배지를 붙이고, 수량은 시나리오에서 자동 계산합니다(랙 수, CRAH 대수, 배관 길이, DLC 대상 kW).

  | 개선안 | 시나리오 변경 | 수량 자동 계산 |
  |---|---|---|
  | 운전 최적화(월 스케줄) | Phase 13 스케줄 | 제어 설정비(고정) |
  | 콜드/핫아일 컨테인먼트 | containment | 랙 수 또는 열 길이 |
  | CRAH 증설 / 교체 | per_side, unit_airflow | 대수 |
  | 배관 방식 변경 | topology | 배관 길이(piping 결과) |
  | 프리쿨링 열교환기 설치 | free_cooling: true | 설계 냉방 kW |
  | DLC 전환 (선택 랙) | 랙 φ (Phase 15) | 대상 IT kW |
  | 액침 전환 | architecture | IT kW |

- **결과**
  - 개선안·패키지 순위 표: 투자비, 연 절감액, 단순·할인 회수기간, NPV, IRR, 탄소 절감 t/년
  - 누적 현금흐름 선 그래프(선택한 2~4개)
  - "개별 합 vs 함께 적용" 차이 안내(상호작용)
  - 민감도 막대(전기요금 ±20%, 투자비 ±30%, IT 증설률 ±50%)
  - CSV 내보내기

### 9.2 설계

- **스키마**: `Scenario.economics: Economics | None`

  ```python
  class TouBand(_In):  months: list[int]; hours: list[int]; krw_per_kwh: PosNum
  class Tariff(_In):   mode: Literal["flat", "tou"] = "flat"; flat_krw_per_kwh: PosNum | None = None
                       tou: list[TouBand] | None = None; demand_krw_per_kw_month: NonNeg = 0
  class Measure(_In):  id: str; enabled: bool = True; capex_krw: NonNeg | None = None
                       unit_cost_krw: NonNeg | None = None; om_krw_per_year: float = 0; life_years: int = 15
                       params: dict = {}      # 개선안별 설정 (예: 대상 랙 id)
  class Economics(_In): tariff: Tariff; water_krw_per_m3: NonNeg = 0; co2_kg_per_kwh: NonNeg | None = None
                        discount_rate: Fraction = 0.05; years: int = Field(15, ge=1, le=40)
                        energy_escalation: float = 0.0; it_growth_pct_per_year: float = 0.0
                        measures: list[Measure] = []
  ```

- **평가** (`hvac/invest.py`)
  1. 기준안과 개선안(또는 패키지)마다 시나리오를 바꿔 **연성 모델 연간 시간별 전력**(plant_vec)과 엔진 보급수를 구합니다. 시간별 값이 있어 계절·시간대 요금을 정확히 적용할 수 있습니다.
  2. 연도 y의 IT 증설은 연간 계산의 증설률을 연도별로 적용합니다. 계산량을 줄이려 0년·중간·마지막 해 3점만 계산하고 사이를 선형 보간합니다(가정, 화면 표기).
  3. 현금흐름 = 절감액(전력 + 기본요금 + 물) − O&M, 0년차는 −투자비, 수명 < 분석 기간이면 재투자합니다.
  4. NPV·할인 회수기간을 계산하고, IRR은 numpy 이분법으로 구합니다. 해가 없으면 "회수 불가"로 표시합니다.
  5. 패키지는 함께 적용한 시나리오로 다시 계산합니다(단순 합 금지). 화면에 개별 합과의 차이를 표시합니다.
- **기본 단가**: 코드 상수 `INVEST_DEFAULTS`에 두고 모두 "예시값"으로 표시합니다. 출처 정책은 §11에서 결정합니다.
- **모듈·API**
  - `POST /jobs/invest` {scenario, weather_file, measures[], packages[][]}
  - `GET /invest/defaults`

### 9.3 테스트 · 완료 기준

- 손계산 예제(단일 단가, 일정 절감)에서 NPV·IRR·회수기간이 일치
- 계절·시간대 요금이 시간별로 정확히 적용됨(합성 요금표)
- 패키지 ≠ 개별 합인 경우를 재현(컨테인먼트 + 운전 최적화)
- `economics`가 없으면 기존 모든 결과 불변
- 성능: 개선안 6개 + 패키지 3개를 20초 이내(Job)

---

## 10. 충돌 방지 매트릭스

### 10.1 파일별 변경 Phase

| 파일 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 규칙 |
|---|---|---|---|---|---|---|---|---|
| hvac/engine.py | – | – | misc_kw 1줄 | – | – | – | – | 기본 0 → 결과 동일 |
| hvac/schemas.py | Provenance | dataset_id | misc_kw, recirc_scale | ScheduleEntry | – | Rack φ | Economics | Phase 구역 주석 아래에만 추가 |
| hvac/airflow.py | – | – | recirc_scale 1줄 | – | – | 랙 φ | – | 기본값이면 결과 동일 |
| hvac/plant.py | `_hour_terms` 분리 | – | – | – | – | – | – | 동작 불변(스냅샷) |
| hvac/plant_vec.py (신규) | 생성 | – | – | 사용 | 사용 | 사용 | 사용 | 패리티 테스트 필수 |
| hvac/optimize.py | 내부 함수 분리 | – | – | 재사용 | 재사용 | 재사용 | – | 스냅샷 불변 |
| hvac/annual.py | – | it_source | – | schedule, coupled | – | – | – | 새 인자 기본값 = 기존 동작 |
| hvac/itdata.py | tabular로 이전 | dataset() | – | – | – | – | – | 기존 테스트 무수정 통과 |
| hvac/weather.py | – | – | 과거값 범위 재사용 | – | forecast 함수 | – | – | 기존 함수 시그니처 불변 |
| hvac/cad.py | – | – | – | – | – | slot 역할 | – | ROLES 끝에 추가 |
| 신규 모듈 | tabular, provenance | itprofile | bms, calibrate | annual_opt | outlook | mixed, placement | invest | Phase 전용 |
| api/main.py | jobs, datasets | – | bms2, calibration | – | outlook | – | invest | include_router 한 줄씩 |
| frontend/src/nav.ts | 생성 | 배지 | 항목 | 항목 | 항목 | 항목 | 항목 | 한 줄씩 추가 |
| types/api.ts, client.ts | Job·Provenance | Phase 11 구역 | 12 구역 | 13 구역 | 14 구역 | 15 구역 | 16 구역 | 구역 끝에 추가 |

### 10.2 기능 간 상호작용 점검표

| 상황 | 결과 | 보장 장치 |
|---|---|---|
| 보정 후 최적화 | 보정 계수로 최적화 | §2.3 쓰기 소유권 |
| 최적화(스케줄) 후 보정 | 스케줄 유지, 계수만 갱신 → 연간 화면에 "보정 이후 재계산 권장" 배지 | provenance 시각 비교 |
| 스케줄 있음 + 사용자가 운전점 수정 | 현재값만 또는 해당 월 반영 중 선택 | 확인 대화상자 |
| 랙 φ 혼합 + 냉각 방식 비교 탭에서 방식 변경 적용 | "랙별 혼합 설정이 사라집니다" 확인 후 φ_i 초기화 | mixed.sync 규칙 |
| CAD 다시 적용 (랙 바뀜) | 기존 랙 φ·kw_profile은 id가 같으면 유지, 없어진 랙은 보고서에 표시 | 기존 id 승계 규칙 확장 |
| IT 데이터셋 삭제 | 연간·예측이 시나리오 모양으로 대체 + 경고 | 데이터셋 참조 검사 |
| 투자안 미리보기 | 시나리오 복사본에서만 계산, 적용 전 원본 불변 | invest는 쓰기 없음 |
| 오래된 저장 시나리오 열기 | 새 필드 없음 → 기본값, 결과 동일 | C1 + 스냅샷 |

---

## 11. 결정 사항 (2026-10-29 확정) · 구현 시 달라진 점

1. **투자 기본 단가**: 예시값 사용 + "예시 데이터를 이용해 시연 계산한 결과" 문구를 투자 화면·결과·상단에 표시
2. **계절·시간대 전기요금표**: 제외 → 단일 단가 x 연 상승률만
3. **공휴일**: 제외 → 날 유형은 평일/토/일
4. **화면**: 전면 개편 — 5개 화면(데이터·현황·연간·예측·개선) + 시작 가이드·신뢰도. AI 해설·자연어·시나리오 비교·하루 BMS 비교·냉각 방식 비교 탭 제거(API 유지).
   nav 레지스트리(§3.1) 대신 App.tsx 의 화면 목록 한 곳으로 단순화
5. **예보 불확실성**: 제외 → 예보값 하나로 계산, 예보 실패 시 연간 파일 같은 날 값으로 대체(크게 표시)
6. **E2E 화면 테스트**: 제외

구현하며 단순화한 점: IT 데이터셋의 랙별 요일x시 점유율(rack_share_how)은 넣지 않음(합계 프로파일만),
CAD 빈 랙 자리 역할은 `slot`, 연간 최적화 풍량 격자는 0.1 간격, 투자 IT 증설은 3점 계산 후 선형 보간.

---

## 12. 작업 분해 · 일정 (1인 기준 추정)

| Phase | 작업 | 예상 |
|---|---|---|
| 10 | nav 레지스트리·시작 가이드·신뢰도 배지 / provenance + revert / jobs + useJob·JobProgress / datasets / tabular 분리 / plant_vec + 패리티 / optimize 내부 분리 / 스냅샷 | 5~6일 |
| 11 | dataset 집계 / itprofile 대체 사슬 / annual it_source / 데이터셋 카드(달력·날 유형) / 테스트 | 3일 |
| 12 | bms 역할 사전·inspect / 기상 자동 보충 / calibrate 6단계 + LM / 검증·판정 / 마법사 4단계 / 적용·되돌리기 / 테스트 | 5~6일 |
| 13 | 월 제약 / 벡터 평가 / DP 스케줄 / 스칼라 검증 / annual schedule·coupled / 화면(비교·스케줄 표) / 적용 / 테스트 | 4~5일 |
| 14 | forecast fetch / 불확실성 / IT 예측 / outlook·권장·알림 / 브리핑 카드 / 테스트 | 3일 |
| 15 | 랙 φ 스키마·기류·sync / 랙 편집 UI(2D 선택) / placement 증분 기류·탐욕·2-opt / 3D 미리보기 / 테스트 | 6~7일 |
| 16 | Economics 스키마 / invest 현금흐름·IRR·패키지·민감도 / 화면 3구역 / CSV / 테스트 | 4~5일 |
| 합계 | | 약 30~35일 |

**각 Phase 공통 완료 체크리스트**
- [ ] 백엔드 전체 테스트 통과, 스냅샷 불변(또는 명시적 갱신과 사유 기록)
- [ ] `npx tsc -b` 통과, 브라우저 수동 확인(주요 흐름·빈 상태·오류 상태·다크 모드·좁은 화면)
- [ ] 결과 화면에 요약 문장, 모델 표기(엔진/연성), 가정 목록 표시
- [ ] 2초 넘는 계산은 Job과 진행률·취소 제공
- [ ] "적용" 동작에 미리보기와 되돌리기 제공
- [ ] README에 기능·가정·한계 추가, Phase 상태 표 갱신

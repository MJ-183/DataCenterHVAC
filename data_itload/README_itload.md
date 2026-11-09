# IT 부하 데이터 (실측 기반 + 더미)

| 파일 | 내용 | 성격 |
|---|---|---|
| `it_load_measured_ucordoba_hourly.csv` | 2024-03-21 ~ 06-04 (75일) 시간별 전력 [kW], 피상전력 [kVA] | **실측** (원 단위 그대로) |
| `it_load_measured_ucordoba_profile24.csv` | 위 자료의 시각별 평균·평일·주말, 평균=1 모양 | 실측 요약 |
| `it_load_dummy_2MW_from_measured_hourly.csv` | 실측 모양을 설계 2,000 kW·평균 가동률 0.78로 환산한 합계 부하 | **더미** (실측 모양 + 규모 가정) |
| `it_load_dummy_racks_long_14d.csv` | 랙 320개(R01-001 ~ R08-040) 랙별 시간 부하, 마지막 14일 | **더미** |
| `it_rack_inventory_dummy.csv` | 랙별 설계 kW (1~2열 8.5 kW, 3~8열 5.5 kW, 합계 2,000 kW) | **더미** |

## 원본
- J. Garrido-Zafra, R. D. Rodriguez-Cantalejo, A. Gil-de-Castro, A. Moreno-Muñoz (2025),
  "Three-Phase Power System Harmonic Dataset from a University Data Center", 스페인 코르도바 대학교
  https://github.com/joaquinjgz/Dataset_university_data_center
- 저압 3상 계통 10분 간격, 77일. 저장소에 라이선스 표기가 없음 → 외부 배포·상업 이용 전 저작자 확인 필요. 인용 표기할 것.

## 처리
1. 유효전력 부호 반전: README의 "A·B·C상 전류 극성이 반대로 측정됨" 안내에 따름 (중성선은 영향 없음)
2. 타임스탬프(ms, UTC) → 현지시각 Europe/Madrid, 1시간 평균 (10분 값 5개 이상인 시간만)
3. 24시간이 다 있는 날만 사용 (첫날·마지막날·서머타임 전환일 제외 → 75일)

## 해석할 때 주의
- **순수 IT 전력이 아님**: README가 측정 대상을 "서버, UPS, HVAC 등이 있는 데이터센터 저압 계통"으로만 설명.
  공조 전력이 섞였을 수 있음
- 규모가 작음(평균 37 kW). 하루 변동은 작음: 시각별 평균의 최저/최고 = 0.97, 주말/평일 = 0.995
- 더미 파일의 규모(2,000 kW, 평균 가동률 0.78), 랙 설계 kW, 랙별 ±15% 가중·±3% 변동은 모두 가정값

## 시뮬레이터에 넣기 (IT 데이터 가져오기)
- 합계 부하: `it_load_dummy_2MW_from_measured_hourly.csv` + `it_rack_inventory_dummy.csv` 함께 업로드
  (랙 목록이 없으면 가져오기가 측정 최대값을 설계부하로 잡음)
- 랙별 부하: `it_load_dummy_racks_long_14d.csv` + `it_rack_inventory_dummy.csv`

"""
기상청 단기예보 격자 (Lambert 정각원추도법, 5 km) ↔ 위경도.
상수는 기상청 동네예보 격자 변환 표준값 (지구 반경 6371.00877 km, 표준위도 30·60°, 기준점 38°N 126°E = 격자 (43, 136)).
검증: 서울 종로(37.5714, 126.9658) → (60, 127) — 단기예보 활용가이드 예시 격자와 일치
"""
import math

RE, GRID = 6371.00877, 5.0
SLAT1, SLAT2, OLON, OLAT = 30.0, 60.0, 126.0, 38.0
XO, YO = 43, 136

_D = math.pi / 180.0
_re = RE / GRID
_sn = math.log(math.cos(SLAT1 * _D) / math.cos(SLAT2 * _D)) / \
    math.log(math.tan(math.pi * 0.25 + SLAT2 * _D * 0.5) / math.tan(math.pi * 0.25 + SLAT1 * _D * 0.5))
_sf = math.tan(math.pi * 0.25 + SLAT1 * _D * 0.5) ** _sn * math.cos(SLAT1 * _D) / _sn
_ro = _re * _sf / math.tan(math.pi * 0.25 + OLAT * _D * 0.5) ** _sn


def to_grid(lat: float, lon: float) -> tuple[int, int]:
    ra = _re * _sf / math.tan(math.pi * 0.25 + lat * _D * 0.5) ** _sn
    theta = lon * _D - OLON * _D
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= _sn
    return int(ra * math.sin(theta) + XO + 0.5), int(_ro - ra * math.cos(theta) + YO + 0.5)


def to_latlon(nx: float, ny: float) -> tuple[float, float]:
    xn, yn = nx - XO, _ro - ny + YO
    ra = math.copysign(math.hypot(xn, yn), _sn)
    alat = 2.0 * math.atan((_re * _sf / ra) ** (1.0 / _sn)) - math.pi * 0.5
    theta = 0.0 if abs(xn) <= 0 else (math.pi * 0.5 * math.copysign(1, xn) if abs(yn) <= 0 else math.atan2(xn, yn))
    return alat / _D, (theta / _sn + OLON * _D) / _D

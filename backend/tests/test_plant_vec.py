"""벡터화 연성 모델 = 스칼라 plant.energy (무충돌 원칙 C8)"""
import json
import random

import numpy as np
import pytest

from hvac import DATA_DIR, simulate
from hvac.plant import energy, networks
from hvac.plant_vec import day_matrix, energy_matrix
from hvac.schemas import CoolingLayout


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("arch", ["air_chw", "hybrid_dlc", "immersion"])
def test_vector_matches_scalar(arch):
    rnd = random.Random(7)
    sc = sample()
    sc["cooling_plant"]["architecture"] = arch
    cfg = CoolingLayout().model_dump()
    days = []
    for k in range(6):   # 기온을 바꾼 6일 (프리쿨링·부분·칠러 모드가 섞이도록)
        s = json.loads(json.dumps(sc))
        s["weather"]["dry_bulb_c"] = [t - 6 * k for t in s["weather"]["dry_bulb_c"]]
        days.append(simulate(s, 60))
    dm = day_matrix(days)
    for _ in range(15):
        fan = rnd.uniform(5, 60)
        cs = rnd.uniform(7, 20)
        cr = cs + rnd.choice([5, 6, 8, 10])
        nets = networks(sc, cfg, days[0], cr - cs)
        v = energy_matrix(dm, sc, cfg, fan, cs, cr, nets)
        for d, res in enumerate(days):
            hrs, _ = energy(sc, cfg, res, fan, cs, cr, nets)
            for key, hk in (("total", "total_kw"), ("chiller", "chiller_kw"), ("cw_pump", "cw_pump_kw")):
                assert np.allclose(v[key][d], [h[hk] for h in hrs], atol=0.051), (arch, key)
            assert np.allclose(v["free_fraction"][d], [h["free_fraction"] for h in hrs], atol=6e-4)


def test_per_day_setpoints_broadcast():
    sc, cfg = sample(), CoolingLayout().model_dump()
    res = simulate(sc, 60)
    dm = day_matrix([res, res])
    nets = networks(sc, cfg, res, 6.0)
    both = energy_matrix(dm, sc, cfg, [20.0, 40.0], [7.0, 12.0], [13.0, 18.0], nets)
    one = energy_matrix(day_matrix([res]), sc, cfg, 40.0, 12.0, 18.0, nets)
    assert np.allclose(both["total"][1], one["total"][0])

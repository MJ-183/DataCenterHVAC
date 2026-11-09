"""모든 테스트: 데이터셋 저장소·실측 비교 캐시·.env 를 임시 폴더로 (실제 backend/data/datasets 와 사용자 API 키를 건드리지 않도록)"""
import pytest

from hvac import datasets, real_compare, secrets


@pytest.fixture(autouse=True)
def _tmp_datasets(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(datasets, "ROOT", tmp_path_factory.mktemp("datasets"))
    monkeypatch.setattr(real_compare, "CACHE_DIR", tmp_path_factory.mktemp("real_cache"))


@pytest.fixture(autouse=True)
def _tmp_env(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(secrets, "ENV_PATH", tmp_path_factory.mktemp("env") / ".env")

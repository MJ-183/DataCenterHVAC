"""데이터셋 저장소 (Phase 10) - IT 계측·BMS 기간 데이터를 서버 파일로 보관하고 시나리오에는 id 만 둔다."""
import json
import re
import uuid
from datetime import datetime

from .engine import DATA_DIR

KINDS = ("it", "bms", "real")   # real = Phase 19 실측 비교 장기 시간 데이터
ID_RE = re.compile(r"^[0-9a-f]{12}$")
ROOT = DATA_DIR / "datasets"


class DatasetError(ValueError):
    pass


def _path(kind, ds_id):
    if kind not in KINDS or not ID_RE.match(ds_id or ""):
        raise DatasetError("잘못된 데이터셋 id 입니다")
    return ROOT / kind / f"{ds_id}.json"


def save(kind, data, name):
    ds_id = uuid.uuid4().hex[:12]
    p = _path(kind, ds_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {**data, "meta": {**data.get("meta", {}), "id": ds_id, "kind": kind, "name": name[:200],
                             "created": datetime.now().isoformat(timespec="seconds")}}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def load(kind, ds_id):
    p = _path(kind, ds_id)
    if not p.exists():
        raise DatasetError(f"데이터셋 {ds_id} 가 없습니다 (삭제되었을 수 있음)")
    return json.loads(p.read_text(encoding="utf-8"))


def listing(kind=None):
    out = []
    for k in ([kind] if kind else KINDS):
        for p in sorted((ROOT / k).glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8"))["meta"])
            except (ValueError, KeyError):
                continue
    return out


def delete(kind, ds_id):
    p = _path(kind, ds_id)
    if not p.exists():
        raise DatasetError(f"데이터셋 {ds_id} 가 없습니다")
    p.unlink()

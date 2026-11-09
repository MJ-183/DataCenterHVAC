"""
API 키 등 비밀값 (backend/.env). 키는 백엔드에서만 쓰고 프론트로 보내지 않는다 (가린 값만).

- 매 호출마다 .env 를 다시 읽는다: 화면에서 저장하거나 VS Code 로 고쳐도 서버 재시작 없이 반영
- .env 에 값이 있으면 그 값, 비어 있으면 환경변수
"""
import os
import re
from pathlib import Path

from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _path(path):
    return path or ENV_PATH   # 호출 시점에 읽음 (테스트에서 ENV_PATH 를 바꿈)


def get(name: str, path: Path | None = None) -> str | None:
    p = _path(path)
    env = dotenv_values(p) if p.exists() else {}
    return (env.get(name) or "").strip() or os.getenv(name) or None


def put(name: str, value: str, path: Path | None = None) -> None:
    """name=value 를 .env 에 저장 (있으면 그 줄만 교체, 없으면 끝에 추가). 다른 줄·주석은 그대로"""
    p = _path(path)
    if "\n" in value or "\r" in value:
        raise ValueError("값에 줄바꿈을 넣을 수 없습니다")
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    pat = re.compile(rf"^\s*{re.escape(name)}\s*=")
    new = f"{name}={value}"
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = new
            break
    else:
        lines.append(new)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(value: str | None) -> str | None:
    """화면 표시용: 끝 4글자만"""
    if not value:
        return None
    return "•" * 8 + value[-4:] if len(value) > 8 else "•" * len(value)

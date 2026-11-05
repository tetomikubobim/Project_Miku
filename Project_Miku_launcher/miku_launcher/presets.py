"""설정 프리셋: 지금 설정 값을 이름을 붙여 저장해 두고, 나중에 (config.yaml 형식이 바뀐 뒤에도) 불러와 적용한다.

원리
- 프리셋은 중첩된 yaml 이 아니라 '경로: 값' 목록({'llm.groq.model': '...'})으로 저장한다.
  config.yaml 의 줄 순서/들여쓰기/주석이 바뀌어도 프리셋에는 영향이 없다.
- 불러올 때는 항목마다 '지금 형식에 맞는지' 따로 검사한다. 맞는 것만 적용하고, 안 맞는 것은 건너뛰면서 이유를 알려 준다
  (하나가 틀려도 나머지는 적용된다. 건너뛴 항목은 현재 설정에 손대지 않는다).
- 항목 이름이 바뀌면 RENAMES 에 '예전 경로: 새 경로' 를 한 줄 적는다. 예전에 만든 프리셋도 새 이름으로 적용된다.
- 새 config.example.yaml 에 새로 생긴 항목은 프리셋에 없으니 건드리지 않고 기본값 그대로 둔다.
- 프리셋 파일은 런처 폴더의 presets/ 에 JSON 으로 둔다 (저장소에는 올라가지 않는다). API 키는 저장할 때 따로 고르고, 기본은 넣지 않는다.
"""
import json
import os
import re
import time
from dataclasses import dataclass, field

import yaml

from . import yamlpatch
from .server_config import _get

FORMAT = 1

# 항목 이름이 바뀔 때 여기에 적는다: {"예전.경로": "새.경로"}
RENAMES = {}

AUTO_PREFIX = "자동백업-"

_MISSING = object()
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_SECRET_LIKE = re.compile(r"(api_?key|token|secret|password)$", re.I)
_SCALAR = (bool, int, float, str)


def safe_name(name):
    """프리셋 이름을 파일 이름으로 써도 되는 형태로 정리한다. 쓸 수 없으면 ValueError."""
    n = _BAD_NAME.sub("", str(name or "")).strip().strip(".").strip()
    if not n:
        raise ValueError("프리셋 이름을 적어 주세요")
    if len(n) > 60:
        raise ValueError("프리셋 이름은 60자 이내로 적어 주세요")
    if n.split(".")[0].upper() in _RESERVED:
        raise ValueError("이 이름은 Windows 에서 쓸 수 없어요. 다른 이름을 적어 주세요")
    return n


class PresetStore:
    """presets/ 폴더의 프리셋 파일 (이름.json) 을 다룬다."""

    def __init__(self, folder):
        self.folder = folder

    def _path(self, name):
        return os.path.join(self.folder, safe_name(name) + ".json")

    def names(self):
        try:
            files = os.listdir(self.folder)
        except OSError:
            return []
        return sorted(f[:-5] for f in files if f.endswith(".json"))

    def exists(self, name):
        return os.path.isfile(self._path(name))

    def save(self, name, values, secrets_included=False, overwrite=False):
        """프리셋을 저장하고 파일 경로를 돌려준다. 같은 이름이 있으면 overwrite=True 일 때만 덮어쓴다 (아니면 FileExistsError)."""
        name = safe_name(name)
        path = self._path(name)
        if os.path.exists(path) and not overwrite:
            raise FileExistsError(f"'{name}' 프리셋이 이미 있어요")
        os.makedirs(self.folder, exist_ok=True)
        data = {"format": FORMAT, "name": name, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "secrets_included": bool(secrets_included), "values": values}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    def load(self, name):
        path = self._path(name)
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(f"프리셋을 읽을 수 없어요: {e}") from None
        if not isinstance(data, dict) or not isinstance(data.get("values"), dict):
            raise ValueError("프리셋 파일 모양이 올바르지 않아요")
        try:
            fmt = int(data.get("format", 1))
        except (TypeError, ValueError):
            fmt = 1
        if fmt > FORMAT:
            raise ValueError("더 새로운 런처에서 만든 프리셋이에요. 런처를 업데이트한 뒤 불러와 주세요")
        return data

    def delete(self, name):
        try:
            os.remove(self._path(name))
        except FileNotFoundError:
            pass

    def prune(self, prefix=AUTO_PREFIX, keep=5):
        """prefix 로 시작하는 자동 백업 프리셋을 최근 keep 개만 남긴다 (이름에 시각이 들어 있어서 이름순 = 시간순)."""
        old = [n for n in self.names() if n.startswith(prefix)]
        for n in old[:-keep] if keep > 0 else old:
            self.delete(n)


# ---------------------------------------------------------------- 지금 설정 -> 프리셋 값
def _leaves(d):
    """{경로: 단순 값}, {비어 있는 블록 경로}. 리스트처럼 지원하지 않는 값은 건너뛴다."""
    leaves, empties = {}, set()

    def walk(node, prefix):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                if v:
                    walk(v, p)
                else:
                    empties.add(p)
            elif isinstance(v, _SCALAR) or v is None:
                leaves[p] = v

    walk(d, "")
    return leaves, empties


def _is_secret(path, secret_paths):
    return path in secret_paths or bool(_SECRET_LIKE.search(path.rsplit(".", 1)[-1]))


def snapshot(cfg, include_secrets=False):
    """저장할 값 {경로: 값}. config.yaml 의 모든 단순 값 + 설정 화면 항목의 지금 값(파일에 없으면 기본값) + 클라이언트 항목.
    API 키는 include_secrets=True 일 때만 넣는다. 설정 화면의 '읽은 시점'(loaded_mtime)은 건드리지 않는다."""
    data, _ = cfg.load()
    values, _ = _leaves(data)
    saved = cfg.loaded_mtime
    try:
        fields = cfg.fields()
    finally:
        cfg.loaded_mtime = saved
    secret_paths = {f["path"] for f in fields if f["type"] == "secret"}
    for f in fields:
        if f["type"] != "secret" and f.get("value") is not None:
            values[f["path"]] = f["value"]
    for p in [p for p in values if _is_secret(p, secret_paths)]:
        if not include_secrets or values[p] in (None, ""):
            del values[p]
    return values


# ---------------------------------------------------------------- 프리셋 -> 지금 설정 (적용 계획)
@dataclass
class Plan:
    fields: dict = field(default_factory=dict)    # 설정 화면 항목: 검사를 마친 값
    raw: dict = field(default_factory=dict)       # 화면에 없는 단순 값 (현재 형식에 있는 것만)
    same: int = 0                                 # 이미 같은 값이라 건드리지 않는 항목 수
    skipped: list = field(default_factory=list)   # [(경로, 이유)]
    renamed: list = field(default_factory=list)   # [(예전 경로, 새 경로)]
    secrets: set = field(default_factory=set)     # 화면에 값을 보여 주면 안 되는 경로

    @property
    def count(self):
        return len(self.fields) + len(self.raw)

    def describe(self, limit=10):
        lines = [f"적용할 항목 {self.count}개 · 이미 같은 항목 {self.same}개 · 건너뛰는 항목 {len(self.skipped)}개"]
        if self.renamed:
            lines.append(f"이름이 바뀐 항목 {len(self.renamed)}개는 새 이름으로 옮겨서 적용해요")
        shown = list({**self.fields, **self.raw}.items())
        if shown:
            lines.append("")
            lines.append("바뀌는 항목:")
            for p, v in shown[:limit]:
                lines.append(f"  {p} = {'●●●●' if p in self.secrets else repr(v)}")
            if len(shown) > limit:
                lines.append(f"  … 외 {len(shown) - limit}개")
        if self.skipped:
            lines.append("")
            lines.append("건너뛰는 항목 (지금 설정은 그대로 둬요):")
            for p, why in self.skipped[:limit]:
                lines.append(f"  {p}: {why}")
            if len(self.skipped) > limit:
                lines.append(f"  … 외 {len(self.skipped) - limit}개")
        return "\n".join(lines)


def _rename(path):
    seen = set()
    while path in RENAMES and path not in seen:   # 이름이 여러 번 바뀐 경우도 따라간다 (돌고 도는 경우만 막는다)
        seen.add(path)
        path = RENAMES[path]
    return path


def _compatible(ref, value):
    """현재(또는 예시) 값 ref 와 같은 종류의 값인가. ref 가 없으면(사용자가 이름을 정하는 블록) 단순 값이면 된다."""
    if not (isinstance(value, _SCALAR) or value is None):
        return False
    if ref is _MISSING or ref is None or value is None:
        return True
    if isinstance(ref, bool):
        return isinstance(value, bool)
    if isinstance(ref, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


def _kind(v):
    return "켜기/끄기" if isinstance(v, bool) else "숫자" if isinstance(v, (int, float)) else "글자"


def plan(cfg, preset_values):
    """프리셋 값 {경로: 값} 을 지금 설정에 적용하는 계획. 파일은 건드리지 않는다.
    - 설정 화면 항목: 서버의 검사 규칙(범위, 선택지 등)을 통과한 것만.
    - 화면에 없는 항목: config.yaml 이나 config.example.yaml 에 있는 항목(또는 사용자가 이름을 정하는 블록 안)만.
    - 나머지는 건너뛰고 이유를 남긴다."""
    data, _ = cfg.load()
    cur, cur_empty = _leaves(data)
    ex, ex_empty = {}, set()
    try:
        with open(cfg.example_path, encoding="utf-8-sig") as f:
            ex, ex_empty = _leaves(yaml.safe_load(f) or {})
    except (OSError, yaml.YAMLError, AttributeError):
        pass
    free = cur_empty | ex_empty
    by = {f["path"]: f for f in cfg.field_defs()}
    text, _bom, _m = yamlpatch.read_text(cfg.path)
    out = Plan(secrets={p for p, f in by.items() if f["type"] == "secret"})

    for old, value in preset_values.items():
        path = _rename(str(old))
        if path != old:
            out.renamed.append((old, path))
        if not (isinstance(value, _SCALAR) or value is None):
            out.skipped.append((path, "지원하지 않는 값 모양이에요"))
            continue
        f = by.get(path)
        if f is not None:
            if f["type"] == "secret" and value in (None, ""):
                continue
            try:
                clean, _ = cfg.validate({path: value})
            except ValueError as e:
                out.skipped.append((path, str(e)))
                continue
            if path not in clean:
                continue
            v = clean[path]
            if f.get("store") == "client":
                now = cfg.client.read(path.split(".", 1)[1], f.get("default")) if cfg.client else None
                same = str(now) == str(v)
            else:
                same = yamlpatch._same(_get(data, path, f.get("default")), v)
            if same:
                out.same += 1
            else:
                out.fields[path] = v
            continue
        if path in cur or path in ex or any(path.startswith(p + ".") for p in free):
            ref = cur.get(path, ex.get(path, _MISSING))
            if not _compatible(ref, value):
                out.skipped.append((path, f"값 종류가 바뀌었어요 (지금은 {_kind(ref)} 형식)"))
                continue
            if path in cur and yamlpatch._same(cur[path], value):
                out.same += 1
                continue
            try:
                yamlpatch.patch_text(text, {path: value})   # 이 값을 지금 파일 모양에 실제로 넣을 수 있는지 미리 확인
            except yamlpatch.PatchError as e:
                out.skipped.append((path, str(e)))
                continue
            out.raw[path] = value
        else:
            out.skipped.append((path, "지금 설정 형식에 없는 항목이에요"))
    return out

"""서버 폴더의 config.yaml 을 읽고 고치는 서비스. 서버의 settings.py(항목 정의/검증)를 그대로 불러다 쓴다.
서버는 설정 파일을 쓰지 않는다: 쓰는 쪽은 여기 하나뿐이다."""
import importlib.util
import os
import re

import yaml

from . import yamlpatch
from .client_config import ClientConfig, audio_latency_error, audio_latency_value
from .extra_fields import EXTRA_FIELDS


class ConfigUnavailable(Exception):
    pass


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get(d, dotted, default=None):
    cur = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


_PROSODY_RE = re.compile(r"^([+-]?)(\d+(?:\.\d+)?)\s*(%|hz)?$", re.I)


def _prosody(f, v):
    """감정 보정 값 검사: '+8%' / '-4Hz' / '8' -> 부호와 단위가 붙은 글자. 빈 값은 '+0단위'."""
    unit = f["prosody"]
    if not v:
        return f"+0{unit}"
    m = _PROSODY_RE.match(v)
    if not m or (m.group(3) and m.group(3).lower() != unit.lower()):
        raise ValueError(f"'{f['label']}' 은 +8{unit} 처럼 숫자와 {unit} 로 적어 주세요")
    n = float(m.group(2))
    n = int(n) if n == int(n) else n
    return f"{m.group(1) or '+'}{n}{unit}"


class ServerConfig:
    def __init__(self, server_dir, client_dir=None):
        self.dir = server_dir
        self.client = ClientConfig(client_dir) if client_dir else None   # 클라이언트 config.json 에 저장되는 항목(store=client)용
        self.path = os.path.join(server_dir, "config.yaml") if server_dir else ""
        self._mod = None
        self._cache = None       # (mtime_ns, cfg)
        self._last_good = {}     # 마지막으로 정상으로 읽은 설정 (파일이 잠깐 깨져도 포트 등을 잃지 않게)
        self.loaded_mtime = None  # 설정 화면이 마지막으로 읽은 시점 (저장할 때 충돌 확인용)

    # ---------- 읽기 ----------
    def exists(self):
        return bool(self.path) and os.path.isfile(self.path)

    def load(self):
        """(cfg dict, mtime_ns). 문법이 틀렸으면 ConfigUnavailable (메시지는 그대로 보여 줘도 된다)."""
        if not self.exists():
            raise ConfigUnavailable("서버 폴더에 config.yaml 이 없어요 (config.example.yaml 을 복사해서 만드세요)")
        mtime = os.stat(self.path).st_mtime_ns
        if self._cache and self._cache[0] == mtime:
            return self._cache[1], mtime
        try:
            text, _bom, mtime = yamlpatch.read_text(self.path)
            cfg = yaml.safe_load(text) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ConfigUnavailable(f"config.yaml 을 읽을 수 없어요: {e}") from None
        if not isinstance(cfg, dict):
            raise ConfigUnavailable("config.yaml 은 '항목: 값' 형태여야 해요")
        self._cache = (mtime, cfg)
        self._last_good = cfg
        return cfg, mtime

    def cfg_or_empty(self):
        """지금 설정. 파일이 깨졌거나 없으면 마지막으로 정상이던 값 (처음부터 못 읽었으면 빈 값)."""
        try:
            return self.load()[0]
        except ConfigUnavailable:
            return self._last_good

    def host_port(self):
        cfg = self.cfg_or_empty()
        host = str(cfg.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        try:
            port = int(cfg.get("port", 3545))
        except (TypeError, ValueError):
            port = 3545
        return host, port

    def log_files(self):
        """GPT-SoVITS / RVC 가 로그를 쓰는 파일 {'sovits': 경로, 'rvc': 경로}."""
        cfg = self.cfg_or_empty()
        t = cfg.get("tts") or {}
        return {"sovits": os.path.join(self.dir, str((t.get("launch") or {}).get("log_file") or "gptsovits.log")),
                "rvc": os.path.join(self.dir, str(((t.get("rvc") or {}).get("launch") or {}).get("log_file") or "rvc.log"))}

    def rvc_models_dir(self):
        """<tts.rvc.launch.dir>/rvc_models. launch.dir 가 비어 있으면 ''."""
        d = str(_get(self.cfg_or_empty(), "tts.rvc.launch.dir", "") or "").strip()
        return os.path.join(os.path.normpath(d), "rvc_models") if d else ""

    def rvc_model_names(self):
        """rvc_models 안의 모델 폴더 이름들 (정렬)."""
        d = self.rvc_models_dir()
        try:
            return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n)))
        except OSError:
            return []

    # ---------- 항목 정의 (서버의 settings.py 를 불러온다) ----------
    def _settings(self):
        if self._mod is None:
            p = os.path.join(self.dir, "settings.py")
            if not os.path.isfile(p):
                raise ConfigUnavailable("서버 폴더에 settings.py 가 없어요 (서버 버전이 맞는지 확인하세요)")
            self._mod = _load_module(p, "miku_server_settings")
            if not hasattr(self._mod, "validate"):
                raise ConfigUnavailable("서버가 예전 버전이에요 (settings.validate 가 없어요). 서버 파일을 새 버전으로 바꿔 주세요")
        return self._mod

    def field_defs(self):
        mod = self._settings()
        defs = list(mod.FIELDS)
        have = {f["path"] for f in defs}
        client_ok = bool(self.client and self.client.exists())
        for f in EXTRA_FIELDS:
            if f.get("store") == "client" and not client_ok:
                continue                      # 클라이언트 폴더/config.json 을 못 찾으면 이 항목은 보이지 않는다
            if f["path"] not in have:
                defs.append(f)
        # 목록이 파일 시스템에서 정해지는 항목 (RVC 모델 폴더)
        cfg = self.cfg_or_empty()
        for i, f in enumerate(defs):
            if f.get("dynamic") == "rvc_models":
                f = dict(f)
                names = self.rvc_model_names()
                cur = str(_get(cfg, f["path"], "") or "")
                if cur and cur not in names:
                    names.append(cur)      # 폴더가 없어졌어도 지금 값은 목록에 남긴다 (저장할 때 거부되지 않게)
                f["options"] = [""] + names
                defs[i] = f
        # 같은 그룹끼리 모으되 처음 나온 순서를 지킨다
        order = []
        for f in defs:
            if f["group"] not in order:
                order.append(f["group"])
        return sorted(defs, key=lambda f: order.index(f["group"]))   # sorted 는 안정 정렬이라 그룹 안의 순서는 그대로

    def fields(self):
        """설정 화면용 목록 (현재 값 포함). 비밀 값은 값 대신 set(설정 여부)만 준다."""
        cfg, mtime = self.load()
        self.loaded_mtime = mtime
        out = []
        for f in self.field_defs():
            item = {k: v for k, v in f.items() if k != "default"}
            if f.get("store") == "client":
                v = self.client.read(f["path"].split(".", 1)[1], f.get("default"))
            else:
                v = _get(cfg, f["path"], f.get("default"))
            if f["type"] == "secret":
                item["set"] = bool(v)
                item["value"] = ""
            else:
                item["value"] = v if v is not None else ("" if f["type"] in ("str", "select") else None)
            out.append(item)
        return out

    # ---------- 저장 ----------
    def validate(self, changes):
        mod = self._settings()
        if not isinstance(changes, dict) or not changes:
            raise ValueError("바꿀 항목이 없어요")
        by = {f["path"]: f for f in self.field_defs()}
        clean = {}
        for path, value in changes.items():
            f = by.get(path)
            if f is None:
                raise ValueError(f"바꿀 수 없는 항목이에요: {path}")
            if f["type"] == "secret" and (value is None or value == ""):
                continue
            if f.get("store") == "client":
                v = str(value if value is not None else "").strip()
                err = audio_latency_error(v)
                if err:
                    raise ValueError(err)
                clean[path] = v
                continue
            v = mod.coerce(f, value)
            if f.get("prosody"):
                v = _prosody(f, v)
            clean[path] = v
        return clean, by

    def save(self, changes):
        """검사 -> 바뀐 것만 저장. 서버 항목은 config.yaml 에(주석은 그대로), 클라이언트 항목(store=client)은 클라이언트 config.json 에.
        {'changed', 'restart': [서버를 다시 켜야 적용되는 항목], 'client_restart': [클라이언트를 다시 시작해야 적용되는 항목], 'server_changed': bool}
        (ValueError/PatchError/ConflictError/ClientConfigError 는 그대로 올린다)"""
        clean, by = self.validate(changes)
        cfg, _ = self.load()
        server_clean = {p: v for p, v in clean.items() if by[p].get("store") != "client"}
        client_clean = {p: v for p, v in clean.items() if by[p].get("store") == "client"}
        changed = [p for p, v in server_clean.items() if _get(cfg, p, by[p].get("default")) != v]
        client_changed = []
        for p, v in client_clean.items():
            key = p.split(".", 1)[1]
            before = self.client.read(key, by[p].get("default"))
            after = audio_latency_value(v)
            if (str(after) if after is not None else str(by[p].get("default") or "")) != before:
                client_changed.append((p, key, after))
        if not changed and not client_changed:
            return {"changed": [], "restart": [], "client_restart": [], "server_changed": False}
        if changed:
            self.loaded_mtime = yamlpatch.apply_file(self.path, {p: server_clean[p] for p in changed}, expected_mtime_ns=self.loaded_mtime)
            self._cache = None
        for _p, key, after in client_changed:
            self.client.write(key, after)
        return {"changed": changed + [p for p, _k, _a in client_changed],
                "restart": [by[p]["label"] for p in changed if by[p].get("restart")],
                "client_restart": [by[p]["label"] for p, _k, _a in client_changed if by[p].get("client_restart")],
                "server_changed": bool(changed)}

    # ---------- 예전 config.local.json ----------
    def legacy_local_exists(self):
        return bool(self.dir) and os.path.exists(yamlpatch.local_path(self.dir))

    def migrate_preview(self):
        return yamlpatch.preview_local(self.dir)

    def migrate(self):
        res = yamlpatch.migrate_local(self.dir)
        self._cache = None
        return res

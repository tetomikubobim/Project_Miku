"""런처 자신의 설정 (launcher.json): 서버/클라 폴더 위치, 자동 시작 여부 등. 서버 설정(config.yaml)과는 별개다."""
import json
import os

DEFAULTS = {
    "server_dir": "",          # 비우면 런처 옆의 Project_Miku_server 등을 찾는다
    "client_dir": "",          # 비우면 런처 옆의 Project_Miku 등을 찾는다
    "server_python": "",       # 서버를 돌릴 파이썬 (비우면 서버 폴더의 venv, 없으면 런처의 파이썬)
    "electron": "",            # electron 실행 파일 (비우면 클라 폴더의 node_modules 에서 찾고, 없으면 npm start)
    "auto_start_server": True,
    "auto_start_client": True,
    "start_hidden": False,     # 창 없이 트레이로만 시작
    "show_tray": False,        # 트레이 아이콘 표시. 끄면 창을 닫을 때 런처(와 서버/클라)가 종료된다
    "poll_seconds": 2.0,
}
NAME = "launcher.json"


def _is_server_dir(p):
    return os.path.isfile(os.path.join(p, "server.py")) and (os.path.isfile(os.path.join(p, "config.yaml")) or os.path.isfile(os.path.join(p, "config.example.yaml")))


def _is_client_dir(p):
    return os.path.isfile(os.path.join(p, "package.json")) and os.path.isfile(os.path.join(p, "main.js"))


def detect(base_dir, kind):
    """런처 폴더 주변에서 서버/클라 폴더를 찾는다. 못 찾으면 ''."""
    parent = os.path.dirname(os.path.abspath(base_dir))
    names = {"server": ["Project_Miku_server", "Project_Miku_Server", "miku_server", "server"],
             "client": ["Project_Miku", "Project_Miku_client", "miku_client", "client"]}[kind]
    ok = _is_server_dir if kind == "server" else _is_client_dir
    for root in (parent, base_dir):
        for n in names:
            p = os.path.join(root, n)
            if os.path.isdir(p) and ok(p):
                return os.path.abspath(p)
    return ""


class LauncherSettings:
    def __init__(self, base_dir, data=None):
        self.base_dir = os.path.abspath(base_dir)
        self.data = {**DEFAULTS, **(data or {})}

    @property
    def path(self):
        return os.path.join(self.base_dir, NAME)

    @classmethod
    def load(cls, base_dir):
        s = cls(base_dir)
        try:
            with open(s.path, encoding="utf-8-sig") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                s.data.update({k: v for k, v in raw.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        return s

    def reset(self):
        """모든 항목을 기본값(DEFAULTS)으로 되돌린다 (저장은 save() 로)."""
        self.data = dict(DEFAULTS)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _dir(self, key, kind):
        v = str(self.data.get(key) or "").strip()
        if v:
            return os.path.abspath(v if os.path.isabs(v) else os.path.join(self.base_dir, v))
        return detect(self.base_dir, kind)

    @property
    def server_dir(self):
        return self._dir("server_dir", "server")

    @property
    def client_dir(self):
        return self._dir("client_dir", "client")

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        if k not in DEFAULTS:
            raise KeyError(k)
        self.data[k] = v

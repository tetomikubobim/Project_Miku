"""GitHub 업데이트 테스트: 버전 비교, 릴리스 확인 오류, 안전한 압축 풀기, 사용자 파일 보호, 백업/되돌리기, 전체 흐름.
네트워크는 쓰지 않는다 (requests.get 을 가짜로 바꿔 끼운다)."""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

import requests

from miku_launcher import updater
from miku_launcher.settings_store import DEFAULTS


def _w(path, text="", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8", "newline": ""})) as f:
        f.write(text)


def _r(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


class Resp:
    def __init__(self, status=200, data=None, body=b""):
        self.status_code, self._data, self._body = status, data, body
        self.headers = {"Content-Length": str(len(body))}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_zip(files, top=""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(top + name, text)
    return buf.getvalue()


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.0.0-beta"), (1, 0, 0))
        self.assertIsNone(updater.parse_version("latest"))
        self.assertTrue(updater.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(updater.is_newer("v1.10.0", "1.9.9"))   # 글자 비교면 틀리는 경우
        self.assertTrue(updater.is_newer("1.1", "1.0.9"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0"))
        self.assertFalse(updater.is_newer("0.9", "1.0.0"))
        self.assertTrue(updater.is_newer("1.0.0", ""))          # VERSION 이 없으면 0.0.0 으로 본다
        self.assertFalse(updater.is_newer("latest", "1.0.0"))

    def test_parse_repo(self):
        self.assertEqual(updater.parse_repo("me/Project_Miku"), "me/Project_Miku")
        self.assertEqual(updater.parse_repo("https://github.com/me/Project_Miku"), "me/Project_Miku")
        self.assertEqual(updater.parse_repo("https://github.com/me/Project_Miku.git"), "me/Project_Miku")
        self.assertEqual(updater.parse_repo("github.com/me/Project_Miku/releases/latest"), "me/Project_Miku")
        self.assertEqual(updater.parse_repo(""), "")
        self.assertEqual(updater.parse_repo("not a repo"), "")
        self.assertEqual(updater.parse_repo("https://example.com/a/b"), "")


class FetchTests(unittest.TestCase):
    REL = {"tag_name": "v1.2.0", "name": "1.2.0", "body": "- 고침", "html_url": "https://github.com/me/r/releases/tag/v1.2.0",
           "zipball_url": "https://api.github.com/repos/me/r/zipball/v1.2.0",
           "assets": [{"name": "notes.txt", "browser_download_url": "u0"}, {"name": "MikuLauncher_win.zip", "browser_download_url": "u1", "size": 5}]}

    def test_ok(self):
        info = updater.fetch_latest("me/r", "1.0.0", get=lambda *a, **k: Resp(200, self.REL))
        self.assertTrue(info.available)
        self.assertEqual(info.release.version, "1.2.0")
        self.assertEqual(info.release.launcher_asset["url"], "u1")
        self.assertFalse(updater.fetch_latest("me/r", "1.2.0", get=lambda *a, **k: Resp(200, self.REL)).available)

    def test_errors_do_not_raise(self):
        def boom(*a, **k):
            raise requests.ConnectionError("x")
        for get, word in ((boom, "닿지"), (lambda *a, **k: Resp(404), "릴리스"), (lambda *a, **k: Resp(403), "한도"), (lambda *a, **k: Resp(500), "500")):
            info = updater.fetch_latest("me/r", "1.0.0", get=get)
            self.assertFalse(info.available)
            self.assertIn(word, info.error)
        self.assertIn("비어", updater.fetch_latest("", "1.0.0").error)
        bad = dict(self.REL, tag_name="nightly")
        self.assertIn("버전", updater.fetch_latest("me/r", "1.0.0", get=lambda *a, **k: Resp(200, bad)).error)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_zip_slip_rejected(self):
        z = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("../evil.txt", "x")
        with self.assertRaises(updater.UpdateError):
            updater.safe_extract(z, os.path.join(self.tmp, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_protection_rules(self):
        self.assertTrue(updater.is_ignored("venv/Scripts/python.exe"))
        self.assertTrue(updater.is_ignored("node_modules/a/b.js"))
        self.assertTrue(updater.is_ignored("config.yaml.bak"))
        self.assertFalse(updater.is_ignored("engine.py"))
        self.assertTrue(updater.is_user_file("server", "config.yaml"))
        self.assertTrue(updater.is_user_file("server", "miku.db"))
        self.assertTrue(updater.is_user_file("server", "chat_log/Hiyori/a.json"))
        self.assertFalse(updater.is_user_file("server", "config.example.yaml"))
        self.assertTrue(updater.is_user_file("client", "models/Hiyori/persona.txt"))
        self.assertTrue(updater.is_user_file("client", "config.json"))
        self.assertFalse(updater.is_user_file("client", "config.default.json"))
        self.assertTrue(updater.is_user_file("launcher", "launcher.json"))
        self.assertTrue(updater.is_user_file("launcher", "presets/a.json"))

    def _layout(self):
        src, dst = os.path.join(self.tmp, "src"), os.path.join(self.tmp, "dst")
        for rel, new, old in (("engine.py", "new\n", "old\n"), ("same.py", "a\nb\n", "a\r\nb\r\n"), ("added.py", "x", None),
                              ("config.yaml", "UPSTREAM", "MINE"), ("persona.txt", "UPSTREAM", "MINE"), ("venv/x.py", "v", None),
                              ("chat_log/Hiyori/log.json", "UP", "MINE")):
            _w(os.path.join(src, *rel.split("/")), new)
            if old is not None:
                _w(os.path.join(dst, *rel.split("/")), old)
        return src, dst

    def test_plan_and_apply(self):
        src, dst = self._layout()
        items = updater.build_plan([("server", src, dst, None)])
        act = {i.rel: i.action for i in items}
        self.assertEqual(act["engine.py"], "update")
        self.assertEqual(act["same.py"], "same")            # 줄 끝만 다르면 같은 파일
        self.assertEqual(act["added.py"], "add")
        self.assertEqual(act["config.yaml"], "protected")   # 내 설정
        self.assertEqual(act["persona.txt"], "protected")
        self.assertEqual(act["chat_log/Hiyori/log.json"], "protected")
        self.assertNotIn("venv/x.py", act)
        bk = os.path.join(self.tmp, "bk")
        m = updater.apply_plan(items, bk)
        self.assertEqual(_r(os.path.join(dst, "engine.py")), "new\n")
        self.assertEqual(_r(os.path.join(dst, "config.yaml")), "MINE")
        self.assertEqual(_r(os.path.join(dst, "persona.txt")), "MINE")
        self.assertEqual(_r(os.path.join(dst, "same.py")), "a\r\nb\r\n")   # 안 건드림
        self.assertTrue(os.path.isfile(os.path.join(dst, "added.py")))
        self.assertFalse(os.path.exists(os.path.join(dst, "venv")))
        self.assertEqual(len(m["files"]), 2)
        self.assertEqual(_r(os.path.join(bk, "server", "engine.py")), "old\n")
        self.assertFalse([n for n in os.listdir(dst) if n.endswith(".miku-new")])
        # 되돌리기
        with open(os.path.join(bk, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(m, f)
        self.assertEqual(updater.rollback(bk), 2)
        self.assertEqual(_r(os.path.join(dst, "engine.py")), "old\n")
        self.assertFalse(os.path.exists(os.path.join(dst, "added.py")))

    def test_failure_rolls_back_everything(self):
        src, dst = os.path.join(self.tmp, "s"), os.path.join(self.tmp, "d")
        _w(os.path.join(src, "a.py"), "NEW-A")
        _w(os.path.join(src, "sub", "b.py"), "NEW-B")
        _w(os.path.join(dst, "a.py"), "OLD-A")
        _w(os.path.join(dst, "sub"), "i am a file, not a dir")   # sub/b.py 를 만들 수 없게 한다
        items = updater.build_plan([("server", src, dst, None)])
        with self.assertRaises(updater.UpdateError):
            updater.apply_plan(items, os.path.join(self.tmp, "bk"))
        self.assertEqual(_r(os.path.join(dst, "a.py")), "OLD-A")   # 이미 바꾼 a.py 도 원래대로
        self.assertFalse([n for n in os.listdir(dst) if n.endswith(".miku-new")])

    def test_swap_script(self):
        new, app = os.path.normpath("/x/new/"), os.path.normpath("/x/app/")
        t = updater.swap_script("/x/new/", "/x/app/", 1234)
        self.assertIn('find " 1234 "', t)
        self.assertIn(f'robocopy "{new}" "{app}"', t)
        self.assertIn("launcher.json", t)
        self.assertIn("start", t)
        self.assertNotIn('start ""', updater.swap_script("/x/new", "/x/app", 1, restart=False))


# ---------- 전체 흐름 ----------
class FakeSettings:
    def __init__(self, base, server_dir, client_dir):
        self.data = {**DEFAULTS, "update_repo": "me/r"}
        self.server_dir, self.client_dir, self.base_dir = server_dir, client_dir, base

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        pass


class FakeProc:
    alive = False


class FakePoller:
    last = None

    def check(self):
        return False


class FakeInstaller:
    def __init__(self, ok=()):
        self.status = {m: ("ok", "") for m in ok}
        self.started = []
        self.running = False

    def refresh(self, wait=False):
        pass

    def start(self, ids):
        self.started.append(list(ids))
        return ""


class FakeCore:
    def __init__(self, base, server_dir, client_dir, installed=()):
        self.base_dir = base
        self.settings = FakeSettings(base, server_dir, client_dir)
        self.server, self.client, self.poller = FakeProc(), FakeProc(), FakePoller()
        self.installer = FakeInstaller(installed)
        self._closing = False
        self.logs = []
        self.presets_saved = 0

    def log(self, t):
        self.logs.append(t)

    def _auto_backup_preset(self):
        self.presets_saved += 1


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "app")
        self.base = os.path.join(self.root, "Project_Miku_launcher")
        self.srv = os.path.join(self.root, "Project_Miku_server")
        self.cli = os.path.join(self.root, "Project_Miku_client")
        _w(os.path.join(self.base, "start_launcher.pyw"), "x")
        _w(os.path.join(self.root, "VERSION"), "1.0.0\n")
        _w(os.path.join(self.srv, "server.py"), "old server\n")
        _w(os.path.join(self.srv, "requirements.txt"), "fastapi\n")
        _w(os.path.join(self.srv, "config.yaml"), "api_key: SECRET\n")
        _w(os.path.join(self.srv, "persona.txt"), "내 캐릭터\n")
        _w(os.path.join(self.srv, "chat_log", "Hiyori", "a.json"), "{}")
        _w(os.path.join(self.cli, "main.js"), "old main\n")
        _w(os.path.join(self.cli, "config.json"), '{"serverUrl": "ws://x"}')
        _w(os.path.join(self.cli, "package.json"), '{"v":1}')
        _w(os.path.join(self.cli, "models", "Hiyori", "persona.txt"), "내 모델 설정")
        self.core = FakeCore(self.base, self.srv, self.cli, installed=("server",))
        top = "me-r-abc123/"
        self.zip = make_zip({
            "Project_Miku_server/server.py": "new server\n",
            "Project_Miku_server/requirements.txt": "fastapi\nsilero-vad\n",
            "Project_Miku_server/config.example.yaml": "x: 1\n",
            "Project_Miku_server/persona.txt": "UPSTREAM persona\n",
            "Project_Miku_client/main.js": "new main\n",
            "Project_Miku_client/package.json": '{"v":1}',
            "Project_Miku_client/config.json": "UPSTREAM",
            "Project_Miku_client/models/Hiyori/persona.txt": "UPSTREAM",
            "Project_Miku_launcher/miku_launcher/core.py": "new core\n",
            "Project_Miku_launcher/launcher.json": "UPSTREAM",
            "README.md": "new readme\n", "VERSION": "1.0.0\n",
        }, top)
        self.release = {"tag_name": "v1.1.0", "name": "1.1.0", "body": "고침", "html_url": "h",
                        "zipball_url": "https://api.github.com/repos/me/r/zipball/v1.1.0", "assets": []}

    def get(self, url, **k):
        if url.endswith("/releases/latest"):
            return Resp(200, self.release)
        if "zipball" in url:
            return Resp(200, body=self.zip)
        return Resp(404)

    def svc(self):
        return updater.UpdateService(self.core, get=self.get)

    def test_apply_then_rollback(self):
        s = self.svc()
        self.assertTrue(s.check().available)
        self.assertIn("1.1.0", s.warning())
        self.assertTrue(s.apply(restart_after=False), self.core.logs)
        # 코드는 새것, 사용자 파일은 그대로
        self.assertEqual(_r(os.path.join(self.srv, "server.py")), "new server\n")
        self.assertEqual(_r(os.path.join(self.cli, "main.js")), "new main\n")
        self.assertEqual(_r(os.path.join(self.srv, "config.yaml")), "api_key: SECRET\n")
        self.assertEqual(_r(os.path.join(self.srv, "persona.txt")), "내 캐릭터\n")
        self.assertEqual(_r(os.path.join(self.cli, "config.json")), '{"serverUrl": "ws://x"}')
        self.assertEqual(_r(os.path.join(self.cli, "models", "Hiyori", "persona.txt")), "내 모델 설정")
        self.assertTrue(os.path.isfile(os.path.join(self.srv, "chat_log", "Hiyori", "a.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.srv, "config.example.yaml")))     # 새 파일은 추가
        self.assertEqual(_r(os.path.join(self.root, "README.md")), "new readme\n")
        self.assertEqual(s.current_version(), "1.1.0")
        self.assertEqual(self.core.presets_saved, 1)
        self.assertFalse(s.info.available)
        self.assertNotIn("새 버전", s.warning())          # 설치했으니 '새 버전이 있어요' 는 사라지고 '다시 켜세요' 만 남는다
        # 런처 코드는 소스 실행이라 같이 바뀌고, launcher.json 은 그대로 (없으면 새로 생기지 않게 안 만든다)
        self.assertEqual(_r(os.path.join(self.base, "miku_launcher", "core.py")), "new core\n")
        self.assertTrue(s.needs_restart)
        # 패키지 목록이 바뀌었고 서버 패키지는 설치돼 있으니 다시 설치 (클라는 package.json 이 같아서 아님)
        self.assertEqual(self.core.installer.started, [["server"]])
        # 작업 폴더는 정리됨, 백업은 남음
        self.assertFalse(os.path.exists(os.path.join(self.base, "updates", "work")))
        self.assertEqual(len(s.backups()), 1)
        # 되돌리기
        self.assertTrue(s.rollback_last(), self.core.logs)
        self.assertEqual(_r(os.path.join(self.srv, "server.py")), "old server\n")
        self.assertEqual(_r(os.path.join(self.cli, "main.js")), "old main\n")
        self.assertFalse(os.path.exists(os.path.join(self.srv, "config.example.yaml")))
        self.assertEqual(s.current_version(), "1.0.0")
        self.assertEqual(s.backups(), [])

    def test_up_to_date_does_nothing(self):
        self.release["tag_name"] = "v1.0.0"
        s = self.svc()
        self.assertFalse(s.apply(restart_after=False))
        self.assertEqual(_r(os.path.join(self.srv, "server.py")), "old server\n")

    def test_git_checkout_is_left_alone(self):
        os.makedirs(os.path.join(self.root, ".git"))
        s = self.svc()
        self.assertFalse(s.apply(restart_after=False))
        self.assertEqual(_r(os.path.join(self.srv, "server.py")), "old server\n")
        self.assertTrue(any("git" in t for t in self.core.logs))

    def test_download_failure_changes_nothing(self):
        s = self.svc()
        s.get = lambda url, **k: Resp(200, self.release) if url.endswith("/latest") or "releases" in url else Resp(500)
        self.assertFalse(s.apply(restart_after=False))
        self.assertEqual(_r(os.path.join(self.srv, "server.py")), "old server\n")
        self.assertEqual(s.current_version(), "1.0.0")

    def test_skip_version_hides_warning(self):
        s = self.svc()
        s.check()
        self.core.settings.data["update_skip_version"] = "1.1.0"
        self.assertTrue(s.skipped())
        self.assertEqual(s.warning(), "")

    def test_startup_respects_switches(self):
        calls = []
        s = self.svc()
        s.get = lambda url, **k: (calls.append(url), self.get(url))[1]
        self.core.settings.data["update_check"] = False
        s.startup()
        self.assertEqual(calls, [])                        # 끄면 네트워크에 아무것도 안 한다
        self.core.settings.data.update(update_check=True, update_auto_apply=True)
        s.startup()                                        # 자동 설치
        self.assertEqual(s.current_version(), "1.1.0")
        self.assertEqual(_r(os.path.join(self.srv, "server.py")), "new server\n")

    def test_no_repo_does_nothing(self):
        self.core.settings.data["update_repo"] = ""
        old_default, updater.DEFAULT_REPO = updater.DEFAULT_REPO, ""
        self.addCleanup(setattr, updater, "DEFAULT_REPO", old_default)
        s = self.svc()
        self.assertEqual(s.repo(), "")
        s.startup()
        self.assertIsNone(s.info)


if __name__ == "__main__":
    unittest.main()

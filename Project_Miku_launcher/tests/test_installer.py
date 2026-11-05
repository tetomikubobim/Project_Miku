"""클라이언트 설치 상태 확인 테스트: electron 만 있고 pixi.js 가 8 이 아니면 "설치 필요"로 보여야 한다."""
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from miku_launcher import installer


def _pkg(root, name, version):
    d = os.path.join(root, "node_modules", *name.split("/"))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "version": version}, f)


class ClientCheckTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        exe = os.path.join(self.dir, "node_modules", "electron", "dist", "electron.exe" if installer.WIN else "electron")
        os.makedirs(os.path.dirname(exe))
        open(exe, "w").close()
        self.env = SimpleNamespace(client_dir=self.dir)
        self.mod = installer.ClientPackages()

    def test_ok_with_pixi8(self):
        _pkg(self.dir, "pixi.js", "8.6.1")
        _pkg(self.dir, "untitled-pixi-live2d-engine", "1.4.0")
        state, msg = self.mod.check(self.env)
        self.assertEqual(state, "ok", msg)

    def test_pixi6_needs_reinstall(self):
        _pkg(self.dir, "pixi.js", "6.5.10")
        _pkg(self.dir, "untitled-pixi-live2d-engine", "1.4.0")
        state, msg = self.mod.check(self.env)
        self.assertEqual(state, "missing")
        self.assertIn("6.5.10", msg)

    def test_pixi_missing(self):
        state, msg = self.mod.check(self.env)
        self.assertEqual(state, "missing")
        self.assertIn("없음", msg)

    def test_engine_missing(self):
        _pkg(self.dir, "pixi.js", "8.0.0")
        state, _ = self.mod.check(self.env)
        self.assertEqual(state, "missing")

    def test_broken_package_json(self):
        _pkg(self.dir, "pixi.js", "8.0.0")
        with open(os.path.join(self.dir, "node_modules", "pixi.js", "package.json"), "w") as f:
            f.write("{oops")
        state, _ = self.mod.check(self.env)
        self.assertEqual(state, "missing")

    def test_no_electron(self):
        shutil.rmtree(os.path.join(self.dir, "node_modules", "electron"))
        state, _ = self.mod.check(self.env)
        self.assertEqual(state, "missing")


if __name__ == "__main__":
    unittest.main()

"""config.yaml 만들기(덮어쓰기 금지)와 설정 프리셋 (형식이 바뀌어도 값 유지) 테스트.
서버 폴더는 런처 옆의 Project_Miku_server 를 쓴다 (다르면 MIKU_SERVER_SRC 로 지정)."""
import os
import shutil
import tempfile
import unittest

from miku_launcher import presets, yamlpatch
from miku_launcher.server_config import ConfigUnavailable, ServerConfig

def _slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_SRC = os.environ.get("MIKU_SERVER_SRC") or os.path.join(HERE, "..", "..", "Project_Miku_server")


@unittest.skipUnless(os.path.isfile(os.path.join(SERVER_SRC, "settings.py")), "서버 폴더를 찾지 못했어요 (MIKU_SERVER_SRC)")
class PresetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.srv = os.path.join(self.tmp, "server")
        os.makedirs(self.srv)
        for n in ("settings.py", "config.example.yaml"):
            shutil.copy(os.path.join(SERVER_SRC, n), self.srv)
        self.cfg = ServerConfig(self.srv)
        self.store = presets.PresetStore(os.path.join(self.tmp, "presets"))
        self.addCleanup(presets.RENAMES.clear)

    def _make(self):
        self.cfg.create_from_example()

    def _read(self):
        return _slurp(self.cfg.path)

    @staticmethod
    def _comments(text):
        """줄 끝/단독 주석 글자만 (값이 바뀐 줄도 주석은 그대로여야 한다)."""
        return [l[l.index("#"):].rstrip() for l in text.splitlines() if "#" in l]

    # ---------- config.yaml 만들기 ----------
    def test_create_makes_copy_of_example(self):
        self.assertTrue(self.cfg.can_create())
        self.cfg.create_from_example()
        self.assertEqual(self._read(), _slurp(self.cfg.example_path))
        self.assertFalse(self.cfg.can_create())

    def test_create_never_overwrites(self):
        self._make()
        with open(self.cfg.path, "a", encoding="utf-8") as f:
            f.write("\n# my note\n")
        before = self._read()
        with self.assertRaises(FileExistsError):
            self.cfg.create_from_example()
        self.assertEqual(self._read(), before)
        self.assertFalse(os.path.exists(self.cfg.path + ".bak"))

    def test_create_without_example(self):
        os.remove(self.cfg.example_path)
        self.assertFalse(self.cfg.can_create())
        with self.assertRaises(ConfigUnavailable):
            self.cfg.create_from_example()

    # ---------- 프리셋 저장 ----------
    def test_snapshot_hides_api_keys_by_default(self):
        self._make()
        self.cfg.save({"llm.groq.api_key": "gsk_SECRET123", "llm.groq.model": "my-model"})
        v = presets.snapshot(self.cfg)
        self.assertEqual(v["llm.groq.model"], "my-model")
        self.assertNotIn("llm.groq.api_key", v)
        self.assertNotIn("gsk_SECRET123", str(v))
        v2 = presets.snapshot(self.cfg, include_secrets=True)
        self.assertEqual(v2["llm.groq.api_key"], "gsk_SECRET123")

    def test_store_overwrite_protection_and_names(self):
        self.store.save("a", {"x": 1})
        with self.assertRaises(FileExistsError):
            self.store.save("a", {"x": 2})
        self.store.save("a", {"x": 3}, overwrite=True)
        self.assertEqual(self.store.load("a")["values"], {"x": 3})
        for bad in ("", "   ", "CON", "a" * 61):
            with self.assertRaises(ValueError):
                presets.safe_name(bad)
        self.assertEqual(presets.safe_name('내/설정:1'), "내설정1")

    def test_prune_keeps_latest(self):
        for i in range(8):
            self.store.save(f"{presets.AUTO_PREFIX}2026110{i}", {})
        self.store.save("내 프리셋", {})
        self.store.prune(keep=3)
        names = self.store.names()
        self.assertIn("내 프리셋", names)
        self.assertEqual(len([n for n in names if n.startswith(presets.AUTO_PREFIX)]), 3)
        self.assertIn(presets.AUTO_PREFIX + "20261107", names)

    # ---------- 불러오기 ----------
    def test_roundtrip_restores_values_and_keeps_comments(self):
        self._make()
        self.cfg.save({"llm.groq.model": "model-A", "history_turns": 77, "temperature": 0.3})
        values = presets.snapshot(self.cfg)
        self.cfg.save({"llm.groq.model": "other", "history_turns": 5, "temperature": 1.5})
        before_comments = self._comments(self._read())
        p = presets.plan(self.cfg, values)
        self.assertEqual(p.fields["llm.groq.model"], "model-A")
        self.assertEqual(p.fields["history_turns"], 77)
        self.assertEqual(p.skipped, [])
        res = self.cfg.save(p.fields, raw=p.raw)
        self.assertIn("llm.groq.model", res["changed"])
        cfg2, _ = self.cfg.load()
        self.assertEqual((cfg2["llm"]["groq"]["model"], cfg2["history_turns"], cfg2["temperature"]), ("model-A", 77, 0.3))
        self.assertEqual(self._comments(self._read()), before_comments)

    def test_second_apply_is_noop(self):
        self._make()
        values = presets.snapshot(self.cfg)
        p = presets.plan(self.cfg, values)
        self.assertEqual(p.count, 0)
        self.assertGreater(p.same, 0)

    def test_removed_and_invalid_items_are_skipped_not_fatal(self):
        self._make()
        values = presets.snapshot(self.cfg)
        values["old.removed.key"] = "x"                 # 지금 형식에 없는 항목
        values["llm.provider"] = "no-such-provider"     # 선택지가 사라진 값
        values["history_turns"] = 99999                 # 범위를 벗어난 값
        values["llm.groq.model"] = "keep-this"          # 멀쩡한 값은 적용돼야 함
        p = presets.plan(self.cfg, values)
        skipped = dict(p.skipped)
        self.assertIn("old.removed.key", skipped)
        self.assertIn("llm.provider", skipped)
        self.assertIn("history_turns", skipped)
        self.assertEqual(p.fields["llm.groq.model"], "keep-this")
        self.cfg.save(p.fields, raw=p.raw)
        cfg, _ = self.cfg.load()
        self.assertEqual(cfg["llm"]["groq"]["model"], "keep-this")
        self.assertNotEqual(cfg["llm"]["provider"], "no-such-provider")

    def test_renamed_item_moves_to_new_path(self):
        self._make()
        presets.RENAMES["llm.groq.old_model_name"] = "llm.groq.model"
        p = presets.plan(self.cfg, {"llm.groq.old_model_name": "moved-model"})
        self.assertEqual(p.fields, {"llm.groq.model": "moved-model"})
        self.assertEqual(p.renamed, [("llm.groq.old_model_name", "llm.groq.model")])

    def test_rename_cycle_does_not_hang(self):
        presets.RENAMES.update({"a.x": "b.x", "b.x": "a.x"})
        presets._rename("a.x")

    def test_new_format_keys_get_added_from_old_preset(self):
        """형식이 바뀌어서 config.yaml 에 키가 빠져 있어도, 프리셋 값이 (예시 형식에 있는 항목이면) 다시 들어간다."""
        self._make()
        raw_candidates = [p for p, v in presets._leaves(self.cfg.load()[0])[0].items()
                          if p not in {f["path"] for f in self.cfg.field_defs()} and isinstance(v, str)]
        self.assertTrue(raw_candidates, "화면에 없는 글자 항목이 하나는 있어야 이 테스트를 할 수 있어요")
        path = raw_candidates[0]
        values = {path: "CUSTOM-VALUE"}
        p = presets.plan(self.cfg, values)
        self.assertEqual(p.raw, {path: "CUSTOM-VALUE"})
        self.cfg.save({}, raw=p.raw)
        cur = presets._leaves(self.cfg.load()[0])[0]
        self.assertEqual(cur[path], "CUSTOM-VALUE")

    def test_key_missing_from_config_gets_added_back(self):
        """새 형식으로 config.yaml 을 다시 만들었더니 키 한 줄이 빠진 상황: 프리셋 값이 그 자리에 다시 들어간다."""
        self._make()
        by = {f["path"] for f in self.cfg.field_defs()}
        leaves = presets._leaves(self.cfg.load()[0])[0]
        path = next(p for p, v in leaves.items() if p not in by and isinstance(v, str) and p.count(".") >= 1)
        key = path.rsplit(".", 1)[1]
        lines = self._read().splitlines(keepends=True)
        kept = [l for l in lines if not l.strip().startswith(key + ":")]
        self.assertEqual(len(kept), len(lines) - 1, "지울 줄이 정확히 하나여야 해요")
        with open(self.cfg.path, "w", encoding="utf-8", newline="") as f:
            f.write("".join(kept))
        self.assertNotIn(path, presets._leaves(self.cfg.load()[0])[0])
        p = presets.plan(self.cfg, {path: "RESTORED"})
        self.assertEqual(p.raw, {path: "RESTORED"})
        self.cfg.save({}, raw=p.raw)
        self.assertEqual(presets._leaves(self.cfg.load()[0])[0][path], "RESTORED")

    def test_wrong_kind_for_raw_item_is_skipped(self):
        self._make()
        by = {f["path"] for f in self.cfg.field_defs()}
        leaves = presets._leaves(self.cfg.load()[0])[0]
        path = next(p for p, v in leaves.items() if p not in by and isinstance(v, str))
        p = presets.plan(self.cfg, {path: 123})
        self.assertEqual(p.raw, {})
        self.assertEqual(len(p.skipped), 1)

    def test_free_form_block_accepts_new_entries(self):
        self._make()
        p = presets.plan(self.cfg, {"tts.emotion_refs.sad.path": "C:/ref/sad.wav"})
        self.assertEqual(p.skipped, [], p.skipped)
        self.cfg.save({}, raw=p.raw)
        self.assertEqual(self.cfg.load()[0]["tts"]["emotion_refs"]["sad"]["path"], "C:/ref/sad.wav")

    def test_secret_is_masked_in_description(self):
        self._make()
        p = presets.plan(self.cfg, {"llm.groq.api_key": "gsk_TOPSECRET"})
        self.assertEqual(p.fields["llm.groq.api_key"], "gsk_TOPSECRET")
        self.assertNotIn("gsk_TOPSECRET", p.describe())

    def test_preset_without_keys_keeps_existing_keys(self):
        self._make()
        self.cfg.save({"llm.groq.api_key": "gsk_KEEP"})
        values = presets.snapshot(self.cfg)        # 키 없이 저장
        p = presets.plan(self.cfg, values)
        self.cfg.save(p.fields, raw=p.raw) if p.count else None
        self.assertEqual(self.cfg.load()[0]["llm"]["groq"]["api_key"], "gsk_KEEP")

    def test_future_format_preset_is_refused(self):
        self.store.save("x", {})
        import json
        path = self.store._path("x")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d["format"] = 99
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        with self.assertRaises(ValueError):
            self.store.load("x")


if __name__ == "__main__":
    unittest.main()

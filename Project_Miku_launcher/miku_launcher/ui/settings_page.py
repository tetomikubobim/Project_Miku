"""서버 설정 탭: 서버의 config.yaml 을 항목별로 고친다 (LLM, 음성, GPT-SoVITS, 음성 인식, 검색, 기억, 주소/경로).
저장하면 config.yaml 의 주석은 그대로 두고 값만 바뀌고, 서버가 켜져 있으면 바로 다시 읽어 적용한다. 서버를 다시 켜야 하는 항목은 ↻ 로 표시한다."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import presets, yamlpatch
from ..client_config import ClientConfigError
from ..server_config import ConfigUnavailable

INT_MAX = 2_000_000_000


def _make_editor(f):
    """항목 정의 하나 -> (편집 위젯, 현재 값을 돌려주는 함수). 비밀 값은 입력한 것만 돌려준다 (비면 ''=그대로 두기)."""
    t, v = f["type"], f.get("value")
    if t == "bool":
        w = QCheckBox()
        w.setChecked(bool(v))
        return w, w.isChecked
    if t == "int":
        w = QSpinBox()
        lo, hi = int(f.get("min", 0)), int(f.get("max", INT_MAX))
        cur = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else lo
        w.setRange(min(lo, cur), max(hi, cur))
        w.setValue(cur)
        return w, w.value
    if t == "float":
        w = QDoubleSpinBox()
        lo, hi = float(f.get("min", 0.0)), float(f.get("max", 1_000_000.0))
        cur = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else lo
        w.setDecimals(3)
        w.setRange(min(lo, cur), max(hi, cur))
        w.setSingleStep(0.05 if hi - lo <= 10 else 1.0)
        w.setValue(cur)
        return w, w.value
    if t == "select":
        w = QComboBox()
        opts = list(f.get("options") or [])
        if f.get("allow_empty"):      # 비워 둘 수 있는 목록 (맨 위가 빈 칸)
            cur = "" if v in (None, "") else str(v)
            if "" not in opts:
                opts.insert(0, "")
        else:
            cur = str(v) if v not in (None, "") else (opts[0] if opts else "")
        if cur not in opts:
            opts.append(cur)
        w.addItems(opts)
        w.setCurrentText(cur)
        return w, w.currentText
    w = QLineEdit()
    if t == "secret":
        w.setEchoMode(QLineEdit.EchoMode.Password)
        w.setPlaceholderText("설정됨 — 바꾸려면 새 값을 입력" if f.get("set") else "설정 안 됨")
        return w, lambda: w.text().strip()
    w.setText("" if v is None else str(v))
    if f.get("hint"):
        w.setPlaceholderText(f["hint"])
    return w, lambda: w.text()


class SettingsPage(QWidget):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.path_label = QLabel("")
        self.path_label.setStyleSheet("color: gray;")
        top.addWidget(self.path_label, 1)
        self.create_btn = QPushButton("config.example.yaml 로 config.yaml 만들기")   # config.yaml 이 없을 때만 보임. 이미 있으면 절대 덮어쓰지 않음
        self.create_btn.clicked.connect(lambda: self.create_config())
        self.create_btn.setVisible(False)
        top.addWidget(self.create_btn)
        self.merge_btn = QPushButton("설정 병합 (config.local.json → config.yaml)")
        self.merge_btn.clicked.connect(self.merge_legacy)
        top.addWidget(self.merge_btn)
        reload_btn = QPushButton("다시 불러오기")
        reload_btn.clicked.connect(self.reload)
        top.addWidget(reload_btn)
        lay.addLayout(top)
        pre = QHBoxLayout()
        pre.addWidget(QLabel("프리셋"))
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(220)
        pre.addWidget(self.preset_box, 1)
        self.preset_load_btn = QPushButton("불러오기")
        self.preset_load_btn.setToolTip("고른 프리셋의 값을 지금 설정에 적용해요. 지금 형식에 안 맞는 항목은 건너뛰고 알려 줘요")
        self.preset_load_btn.clicked.connect(lambda: self.load_preset())
        pre.addWidget(self.preset_load_btn)
        self.preset_save_btn = QPushButton("현재 설정 저장…")
        self.preset_save_btn.setToolTip("지금 config.yaml 의 값을 이름을 붙여 프리셋으로 저장해요 (런처 폴더의 presets/)")
        self.preset_save_btn.clicked.connect(lambda: self.save_preset())
        pre.addWidget(self.preset_save_btn)
        self.preset_del_btn = QPushButton("삭제")
        self.preset_del_btn.clicked.connect(lambda: self.delete_preset())
        pre.addWidget(self.preset_del_btn)
        lay.addLayout(pre)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        bottom = QHBoxLayout()
        note = QLabel("↻ 표시 항목은 서버를 다시 켜야 적용돼요. 그 밖의 항목은 저장하면 바로 적용돼요.")
        note.setStyleSheet("color: gray;")
        bottom.addWidget(note, 1)
        self.save_btn = QPushButton("저장")
        self.save_btn.clicked.connect(lambda: self.save(restart=False))
        bottom.addWidget(self.save_btn)
        self.save_restart_btn = QPushButton("저장하고 서버 재시작")
        self.save_restart_btn.clicked.connect(lambda: self.save(restart=True))
        bottom.addWidget(self.save_restart_btn)
        lay.addLayout(bottom)
        self.editors = {}   # path -> (getter, 처음 값)
        self._config_ok = False
        self.reload()

    # ---------- 불러오기 ----------
    def reload(self, message=""):
        self.tabs.clear()
        self.editors.clear()
        cfg = self.core.config
        self.path_label.setText(cfg.path or "서버 폴더를 찾지 못했어요")
        self.merge_btn.setVisible(cfg.legacy_local_exists())
        self.create_btn.setVisible(cfg.can_create())
        try:
            fields = cfg.fields()
        except ConfigUnavailable as e:
            text = ("config.yaml 이 아직 없어요. 위의 [config.example.yaml 로 config.yaml 만들기] 버튼을 눌러 주세요."
                    if cfg.can_create() else str(e))
            self.tabs.addTab(QLabel(text), "서버 설정")
            self.msg.setText(message or text)
            self._set_enabled(False)
            self.refresh_presets()
            return
        self._set_enabled(True)
        self.refresh_presets()
        groups = {}
        for f in fields:
            groups.setdefault(f["group"], []).append(f)
        for name, items in groups.items():
            body = QWidget()
            form = QFormLayout(body)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            for f in items:
                editor, getter = _make_editor(f)
                label = QLabel(f["label"] + (" ↻" if f.get("restart") else ""))
                label.setWordWrap(True)
                if f.get("hint"):
                    label.setToolTip(f["hint"])
                    editor.setToolTip(f["hint"])
                form.addRow(label, editor)
                self.editors[f["path"]] = (getter, getter())
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(body)
            self.tabs.addTab(scroll, name)
        self.msg.setText(message)

    def _set_enabled(self, on):
        self._config_ok = on
        self.save_btn.setEnabled(on)
        self.save_restart_btn.setEnabled(on)
        self.preset_save_btn.setEnabled(on)

    # ---------- 저장 ----------
    def _edited(self):
        out = {}
        for path, (getter, first) in self.editors.items():
            now = getter()
            if now != first:
                out[path] = now
        return out

    def save(self, restart=False):
        changes = self._edited()
        if not changes:
            self.msg.setText("바뀐 게 없어요.")
            if restart:
                self._restart_server()
            return
        try:
            res = self.core.save_server_config(changes)
        except ValueError as e:
            self.msg.setText(f"저장하지 않았어요: {e}")
            return
        except yamlpatch.ConflictError as e:
            self.reload(f"저장하지 않았어요: {e}")
            return
        except (yamlpatch.PatchError, ConfigUnavailable, ClientConfigError) as e:
            self.msg.setText(f"저장하지 않았어요: {e}")
            return
        self.reload(self._result_text(res, "저장했어요"))
        if restart:
            self._restart_server()

    def _result_text(self, res, verb):
        parts = [f"{verb} ({len(res['changed'])}개)."]
        if res["reloaded"]:
            parts.append("서버에 바로 반영했어요.")
        elif res["reload_error"]:
            parts.append(f"서버에 알리지 못했어요: {res['reload_error']}")
        elif res["changed"] and not self.core.poller.last:
            parts.append("서버를 켤 때 적용돼요.")
        if res["restart"]:
            parts.append("서버를 다시 켜야 적용되는 항목: " + ", ".join(res["restart"]))
        if res.get("client_restart"):
            parts.append("클라이언트를 다시 시작해야 적용되는 항목: " + ", ".join(res["client_restart"]))
        return " ".join(parts)

    # ---------- config.yaml 처음 만들기 (덮어쓰기 금지) ----------
    def create_config(self):
        try:
            self.core.create_server_config()
        except FileExistsError as e:     # 그 사이에 생겼거나 이미 있었음: 건드리지 않고 알려 주기만 한다
            self.reload(str(e))
            return
        except (ConfigUnavailable, OSError) as e:
            self.msg.setText(f"만들지 못했어요: {e}")
            return
        self.reload("config.yaml 을 만들었어요. 사용할 LLM 과 API 키를 입력하고 저장하세요.")

    # ---------- 프리셋 ----------
    def refresh_presets(self, select=None):
        names = self.core.presets.names()
        cur = select or self.preset_box.currentText()
        self.preset_box.clear()
        self.preset_box.addItems(names)
        if cur in names:
            self.preset_box.setCurrentText(cur)
        self.preset_load_btn.setEnabled(bool(names) and self._config_ok)
        self.preset_del_btn.setEnabled(bool(names))

    def save_preset(self):
        Yes, No, Cancel = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No, QMessageBox.StandardButton.Cancel
        if self._edited() and QMessageBox.question(
                self, "프리셋 저장", "화면에서 바꾼 값이 아직 저장되지 않았어요.\n프리셋에는 저장된 값(config.yaml)이 들어가요. 계속할까요?") != Yes:
            return
        name, ok = QInputDialog.getText(self, "프리셋 저장", "프리셋 이름:")
        if not ok:
            return
        try:
            name = presets.safe_name(name)
        except ValueError as e:
            self.msg.setText(f"저장하지 않았어요: {e}")
            return
        overwrite = False
        if self.core.presets.exists(name):
            if QMessageBox.question(self, "프리셋 저장", f"'{name}' 프리셋이 이미 있어요. 덮어쓸까요?") != Yes:
                return
            overwrite = True
        keys = QMessageBox.question(
            self, "프리셋 저장",
            "API 키도 프리셋에 포함할까요?\n(포함하면 프리셋 파일에 키가 그대로 적혀요. 남과 나눌 프리셋이라면 '아니오'를 고르세요. "
            "키를 빼고 저장해도 불러올 때 지금 키는 그대로 남아요.)",
            Yes | No | Cancel, No)
        if keys == Cancel:
            return
        try:
            self.core.save_preset(name, include_secrets=(keys == Yes), overwrite=overwrite)
        except (ValueError, OSError, ConfigUnavailable, yamlpatch.PatchError, ClientConfigError) as e:
            self.msg.setText(f"저장하지 않았어요: {e}")
            return
        self.refresh_presets(select=name)
        self.msg.setText(f"프리셋 '{name}' 을 저장했어요.")

    def load_preset(self):
        name = self.preset_box.currentText()
        if not name:
            self.msg.setText("불러올 프리셋을 먼저 고르세요.")
            return
        try:
            plan = self.core.plan_preset(name)
        except (ValueError, OSError, ConfigUnavailable, yamlpatch.PatchError) as e:
            self.msg.setText(f"불러오지 못했어요: {e}")
            return
        if not plan.count:
            QMessageBox.information(self, "프리셋 불러오기", f"'{name}' 프리셋에서 바꿀 항목이 없어요.\n\n" + plan.describe())
            return
        text = f"프리셋 '{name}' 을 적용할까요?\n\n" + plan.describe(limit=15) + "\n\n적용하기 전 config.yaml 은 config.yaml.bak 으로 남아요."
        if self._edited():
            text += "\n(화면에서 바꾸고 저장하지 않은 값은 사라져요)"
        if QMessageBox.question(self, "프리셋 불러오기", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self.core.apply_preset(plan)
        except ValueError as e:
            self.msg.setText(f"적용하지 않았어요: {e}")
            return
        except yamlpatch.ConflictError as e:
            self.reload(f"적용하지 않았어요: {e}")
            return
        except (yamlpatch.PatchError, ConfigUnavailable, ClientConfigError, OSError) as e:
            self.msg.setText(f"적용하지 않았어요: {e}")
            return
        extra = f" 건너뛴 항목 {len(plan.skipped)}개." if plan.skipped else ""
        self.reload(self._result_text(res, f"프리셋 '{name}' 을 적용했어요") + extra)

    def delete_preset(self):
        name = self.preset_box.currentText()
        if not name:
            return
        if QMessageBox.question(self, "프리셋 삭제", f"프리셋 '{name}' 을 지울까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.core.presets.delete(name)
        except OSError as e:
            self.msg.setText(f"지우지 못했어요: {e}")
            return
        self.refresh_presets()
        self.msg.setText(f"프리셋 '{name}' 을 지웠어요.")

    def _restart_server(self):
        self.core.run_async("server", self.core.restart_server if self.core.server.alive or self.core.poller.last else self.core.start_server)

    # ---------- 예전 config.local.json 합치기 ----------
    def merge_legacy(self):
        try:
            diff = self.core.config.migrate_preview()
        except yamlpatch.PatchError as e:
            QMessageBox.warning(self, "설정 병합", str(e))
            return
        lines = []
        for path, have, new in diff:
            old = "(없음)" if have is KeyError else repr(have)
            lines.append(f"{path}:  {old}  →  {new!r}")
        text = ("config.local.json 의 값을 config.yaml 에 합칠까요?\n\n"
                "합치면 지금 서버가 쓰던 값(local 이 yaml 보다 우선하던 값) 그대로 config.yaml 에 들어가고, local 파일은 .migrated.bak 으로 보관돼요.\n"
                + ("\n바뀌는 항목:\n" + "\n".join(lines) if lines else "\n(config.yaml 과 다른 값이 없어요. local 파일만 치워져요.)"))
        if QMessageBox.question(self, "설정 병합", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self.core.migrate_local_config()
        except yamlpatch.PatchError as e:
            QMessageBox.warning(self, "설정 병합", f"합치지 못했어요: {e}")
            return
        self.reload(f"합쳤어요 ({len(res)}개). 서버가 켜져 있으면 바로 반영됐어요.")

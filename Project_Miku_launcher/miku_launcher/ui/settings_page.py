"""서버 설정 탭: 서버의 config.yaml 을 항목별로 고친다 (LLM, 음성, GPT-SoVITS, 음성 인식, 검색, 기억, 주소/경로).
저장하면 config.yaml 의 주석은 그대로 두고 값만 바뀌고, 서버가 켜져 있으면 바로 다시 읽어 적용한다. 서버를 다시 켜야 하는 항목은 ↻ 로 표시한다."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import yamlpatch
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
        self.merge_btn = QPushButton("설정 병합 (config.local.json → config.yaml)")
        self.merge_btn.clicked.connect(self.merge_legacy)
        top.addWidget(self.merge_btn)
        reload_btn = QPushButton("다시 불러오기")
        reload_btn.clicked.connect(self.reload)
        top.addWidget(reload_btn)
        lay.addLayout(top)
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
        self.reload()

    # ---------- 불러오기 ----------
    def reload(self, message=""):
        self.tabs.clear()
        self.editors.clear()
        cfg = self.core.config
        self.path_label.setText(cfg.path or "서버 폴더를 찾지 못했어요")
        self.merge_btn.setVisible(cfg.legacy_local_exists())
        try:
            fields = cfg.fields()
        except ConfigUnavailable as e:
            self.tabs.addTab(QLabel(str(e)), "서버 설정")
            self.msg.setText(str(e))
            self._set_enabled(False)
            return
        self._set_enabled(True)
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
        self.save_btn.setEnabled(on)
        self.save_restart_btn.setEnabled(on)

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
        n = len(res["changed"])
        parts = [f"저장했어요 ({n}개)."]
        if res["reloaded"]:
            parts.append("서버에 바로 반영했어요.")
        elif res["reload_error"]:
            parts.append(f"서버에 알리지 못했어요: {res['reload_error']}")
        elif not self.core.poller.last:
            parts.append("서버를 켤 때 적용돼요.")
        if res["restart"]:
            parts.append("서버를 다시 켜야 적용되는 항목: " + ", ".join(res["restart"]))
        if res.get("client_restart"):
            parts.append("클라이언트를 다시 시작해야 적용되는 항목: " + ", ".join(res["client_restart"]))
        self.reload(" ".join(parts))
        if restart:
            self._restart_server()

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

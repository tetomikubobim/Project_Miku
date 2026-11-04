"""설치 탭: 필수/선택 모듈이 설치돼 있는지 불빛으로 보여 주고, 버튼으로 바로 설치한다."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from ..installer import GROUPS
from .widgets import StatusLight

STATE_LIGHT = {"ok": "ok", "partial": "warn", "missing": "stopped", "unknown": "unknown"}
STATE_TEXT = {"ok": "설치됨", "partial": "일부만 설치됨", "missing": "설치 안 됨", "unknown": "확인 중…"}


REMOVE_NOTE = {
    "server": "서버 폴더의 venv 만 지워요. 서버 코드와 설정은 그대로예요.",
    "client": "클라 폴더의 node_modules 만 지워요.",
    "mic_stt": "faster-whisper 패키지만 지워요 (내려받은 인식 모델 파일은 그대로).",
    "mic_models": "voice-models 의 VAD / 화자 인증 모델 파일을 지워요.",
    "mic_gpu": "CUDA 라이브러리(cuBLAS/cuDNN)를 지워요.",
    "rvc": "RVC 가상환경(tools/rvc/venv)만 지워요. rvc_models 안의 모델은 그대로예요.",
    "rvc_tsukuyomi": "rvc_models/tsukuyomi 폴더를 지워요.",
    "sovits": "tools/gptsovits 폴더 전체를 지워요. 직접 넣은 참조 음성이나 학습한 모델도 함께 사라져요!",
}


class ModuleRow(QWidget):
    def __init__(self, module, on_install, on_remove, parent=None):
        super().__init__(parent)
        self.module = module
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.light = StatusLight()
        lay.addWidget(self.light)
        col = QVBoxLayout()
        self.title = QLabel(f"<b>{module.title}</b> <span style='color:gray'>· {module.size}</span>")
        self.desc = QLabel(module.desc)
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet("color: gray;")
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.desc)
        col.addWidget(self.detail)
        lay.addLayout(col, 1)
        self.state = QLabel("")
        self.state.setMinimumWidth(90)
        lay.addWidget(self.state)
        self.btn = QPushButton("설치")
        self.btn.setMinimumWidth(80)
        self.btn.clicked.connect(lambda: on_install([module.id]))
        lay.addWidget(self.btn)
        self.del_btn = QPushButton("지우기")
        self.del_btn.setMinimumWidth(70)
        self.del_btn.clicked.connect(lambda: on_remove(module))
        lay.addWidget(self.del_btn)

    def update_state(self, state, detail, busy):
        self.light.set_state(STATE_LIGHT.get(state, "unknown"))
        self.state.setText(STATE_TEXT.get(state, state))
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.btn.setText("다시 설치" if state == "ok" else ("이어서 설치" if state == "partial" else "설치"))
        self.btn.setEnabled(not busy and state != "unknown")
        self.del_btn.setEnabled(not busy and state in ("ok", "partial"))


class InstallPage(QWidget):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.mgr = core.installer
        self.rows = {}
        self.seen = 0
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        note = QLabel("필수 항목을 먼저 설치하세요. 선택 항목은 쓸 때만 설치하면 돼요. 설치 중에는 서버/클라를 꺼야 하는 항목이 있어요.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        top.addWidget(note, 1)
        self.all_btn = QPushButton("필수 모두 설치")
        self.all_btn.clicked.connect(self._install_required)
        top.addWidget(self.all_btn)
        recheck = QPushButton("다시 확인")
        recheck.clicked.connect(self.mgr.refresh)
        top.addWidget(recheck)
        lay.addLayout(top)
        for key, label in GROUPS:
            box = QGroupBox(label)
            v = QVBoxLayout(box)
            for m in self.mgr.modules:
                if m.group == key:
                    row = ModuleRow(m, self._install, self._remove)
                    self.rows[m.id] = row
                    v.addWidget(row)
            lay.addWidget(box)
        prog = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setVisible(False)
        prog.addWidget(self.bar, 1)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.mgr.cancel)
        prog.addWidget(self.cancel_btn)
        lay.addLayout(prog)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(3000)
        self.out.setPlaceholderText("설치 로그가 여기에 나와요")
        lay.addWidget(self.out, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(500)
        self.mgr.refresh()

    def showEvent(self, e):   # 탭을 열 때마다 최신 상태로
        super().showEvent(e)
        if not self.mgr.running:
            self.mgr.refresh()

    def _install(self, ids):
        why = self.mgr.start(ids)
        if why:
            QMessageBox.information(self, "설치", why)

    def _remove(self, module):
        ans = QMessageBox.question(
            self, "지우기", f"'{module.title}' 을(를) 지울까요?\n\n{REMOVE_NOTE.get(module.id, '')}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        why = self.mgr.start_uninstall([module.id])
        if why:
            QMessageBox.information(self, "지우기", why)

    def _install_required(self):
        ids = [m.id for m in self.mgr.modules if m.group == "required" and self.mgr.status[m.id][0] != "ok"]
        if not ids:
            QMessageBox.information(self, "설치", "필수 항목은 모두 설치돼 있어요.")
            return
        self._install(ids)

    def poll(self):
        busy = self.mgr.running
        for mid, row in self.rows.items():
            st, detail = self.mgr.status[mid]
            if busy and mid == self.mgr.current:
                st, detail = "partial", "설치 중…"
            row.update_state(st, detail, busy)
        self.all_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.bar.setVisible(busy)
        if busy:
            f = self.mgr.progress_frac
            self.bar.setRange(0, 100 if f is not None else 0)   # 0~0 이면 진행 중(움직이는 막대)
            if f is not None:
                self.bar.setValue(int(f * 100))
            self.bar.setFormat(self.mgr.progress_text or "설치 중…")
        for seq, text in self.mgr.since(self.seen):
            self.out.appendPlainText(text)
            self.seen = seq

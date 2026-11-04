"""메인 창: 위쪽에 상태 카드(서버/클라/STT/TTS/LLM), 아래에 탭(로그/서버 설정/런처)과 리소스 패널."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from .install_page import InstallPage
from .launcher_page import LauncherPage
from .log_page import LogPage
from .resource_panel import ResourcePanel
from .settings_page import SettingsPage
from .widgets import StatusLight

SERVER_RUNNING = ("ok", "starting", "error")
CLIENT_RUNNING = ("ok", "warn", "starting")


class StatusCard(QFrame):
    def __init__(self, title, buttons=(), parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { border: 1px solid palette(mid); border-radius: 8px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.light = StatusLight()
        head.addWidget(self.light)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; border: none;")
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)
        self.text = QLabel("-")
        self.text.setWordWrap(True)
        self.text.setStyleSheet("border: none;")
        self.text.setMinimumHeight(34)
        self.text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.text)
        self.buttons = {}
        if buttons:
            row = QHBoxLayout()
            for key, label in buttons:
                b = QPushButton(label)
                self.buttons[key] = b
                row.addWidget(b)
            lay.addLayout(row)

    def set(self, state, text):
        self.light.set_state(state)
        self.text.setText(text)


class MainWindow(QMainWindow):
    def __init__(self, core, icon=None, on_close_request=None):
        super().__init__()
        self.core = core
        self.on_close_request = on_close_request
        self.setWindowTitle("Project Miku 런처")
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(1100, 720)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        grid = QGridLayout()
        self.server = StatusCard("서버", [("start", "시작"), ("stop", "중지"), ("restart", "재시작")])
        self.client = StatusCard("클라이언트", [("start", "시작"), ("stop", "중지")])
        self.stt = StatusCard("음성 인식 (STT)")
        self.tts = StatusCard("음성 합성 (TTS)")
        self.llm = StatusCard("LLM")
        for i, c in enumerate((self.server, self.client, self.stt, self.tts, self.llm)):
            grid.addWidget(c, 0, i)
            grid.setColumnStretch(i, 1)
        outer.addLayout(grid)

        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #d29922;")
        self.warn.setVisible(False)
        outer.addWidget(self.warn)

        body = QHBoxLayout()
        self.tabs = QTabWidget()
        self.log_page = LogPage(core)
        self.settings_page = SettingsPage(core)
        self.launcher_page = LauncherPage(core)
        self.install_page = InstallPage(core)
        self.launcher_page.on_reset = self.settings_page.reload
        self.tabs.addTab(self.log_page, "로그")
        self.tabs.addTab(self.settings_page, "서버 설정")
        self.tabs.addTab(self.install_page, "설치")
        self.tabs.addTab(self.launcher_page, "런처")
        body.addWidget(self.tabs, 1)
        self.resources = ResourcePanel()
        body.addWidget(self.resources)
        outer.addLayout(body, 1)

        s, c = core, core
        self.server.buttons["start"].clicked.connect(lambda: s.run_async("server", s.start_server))
        self.server.buttons["stop"].clicked.connect(lambda: s.run_async("server", s.stop_server))
        self.server.buttons["restart"].clicked.connect(lambda: s.run_async("server", s.restart_server))
        self.client.buttons["start"].clicked.connect(lambda: c.run_async("client", c.start_client))
        self.client.buttons["stop"].clicked.connect(lambda: c.run_async("client", c.stop_client))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.last_snapshot = None
        self.refresh()

    def refresh(self):
        snap = self.core.snapshot()
        self.last_snapshot = snap
        sv, cl = snap["server"], snap["client"]
        extra = f" (pid {sv['pid']})" if sv.get("pid") else ""
        self.server.set(sv["state"], sv["text"] + extra)
        self.client.set(cl["state"], cl["text"])
        self.stt.set(snap["stt"]["state"], snap["stt"]["text"])
        self.tts.set(snap["tts"]["state"], snap["tts"]["text"])
        llm = snap["llm"]
        self.llm.set("idle" if llm["provider"] else "unknown",
                     (f"{llm['provider']} · {llm['model']}\n" if llm["provider"] else "서버가 꺼져 있어요\n") + "연결 확인은 로그의 'LLM' 보기에서")
        sb, cb = self.server.buttons, self.client.buttons
        s_run, busy_s = sv["state"] in SERVER_RUNNING, sv["busy"] or sv["state"] == "stopping"
        sb["start"].setEnabled(not s_run and not busy_s)
        sb["stop"].setEnabled(s_run and not busy_s)
        sb["restart"].setEnabled(s_run and not busy_s)
        c_run, busy_c = cl["state"] in CLIENT_RUNNING, cl["busy"] or cl["state"] == "stopping"
        cb["start"].setEnabled(not c_run and not busy_c)
        cb["stop"].setEnabled(c_run and not busy_c)
        if snap["warnings"]:
            self.warn.setText("⚠ " + "\n⚠ ".join(snap["warnings"]))
            self.warn.setVisible(True)
        else:
            self.warn.setVisible(False)
        self.resources.update_from(snap["resources"])
        self.log_page.poll()

    allow_close = False   # 종료 정리가 끝나서 창을 정말로 닫아도 될 때 True (app.py 가 켠다)

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        elif self.on_close_request:
            event.ignore()
            self.on_close_request()   # 창 X: 트레이로 숨기거나(트레이가 있으면) 종료 확인
        else:
            event.accept()

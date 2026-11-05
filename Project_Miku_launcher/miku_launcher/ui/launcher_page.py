"""런처 탭: 서버/클라 폴더 위치와 자동 시작 같은 런처 자신의 설정 (launcher.json)."""
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .update_box import UpdateBox


class PathRow(QWidget):
    def __init__(self, caption, is_dir, parent=None):
        super().__init__(parent)
        self.caption, self.is_dir = caption, is_dir
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        lay.addWidget(self.edit, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self.browse)
        lay.addWidget(b)

    def browse(self):
        start = self.edit.text() or os.path.expanduser("~")
        if self.is_dir:
            p = QFileDialog.getExistingDirectory(self, self.caption, start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, self.caption, start)
        if p:
            self.edit.setText(os.path.normpath(p))


class LauncherPage(QWidget):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.on_saved = None
        self.on_reset = None   # 초기화가 끝난 뒤 다른 화면(서버 설정 탭)도 새로 읽게 하는 함수
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.server_dir = PathRow("서버 폴더 (server.py 가 있는 곳)", True)
        self.client_dir = PathRow("클라 폴더 (package.json 이 있는 곳)", True)
        self.server_py = PathRow("서버를 돌릴 파이썬", False)
        self.electron = PathRow("electron 실행 파일 (비우면 클라 폴더에서 찾아요)", False)
        form.addRow("서버 폴더", self.server_dir)
        form.addRow("클라 폴더", self.client_dir)
        form.addRow("서버 파이썬", self.server_py)
        form.addRow("electron", self.electron)
        self.auto_server = QCheckBox("런처를 켜면 서버도 같이 켜기")
        self.auto_client = QCheckBox("서버가 준비되면 클라도 같이 켜기")
        self.hidden = QCheckBox("창 없이 트레이로만 시작 (트레이 아이콘을 켠 경우만)")
        self.show_tray = QCheckBox("트레이 아이콘 보이기 (끄면 창을 닫을 때 런처와 서버/클라도 같이 종료돼요)")
        for c in (self.auto_server, self.auto_client, self.hidden, self.show_tray):
            form.addRow("", c)
        lay.addLayout(form)
        self.info = QLabel("")
        self.info.setStyleSheet("color: gray;")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.updates = UpdateBox(core)
        lay.addWidget(self.updates)
        row = QHBoxLayout()
        reset = QPushButton("모든 설정 기본값으로 초기화…")
        reset.setToolTip("런처 / 서버(config.yaml) / 클라(config.json) 설정을 배포 때의 기본값으로 되돌려요")
        reset.clicked.connect(self.reset_all)
        row.addWidget(reset)
        row.addStretch(1)
        save = QPushButton("저장")
        save.clicked.connect(self.save)
        row.addWidget(save)
        lay.addLayout(row)
        lay.addStretch(1)
        self.load()

    def load(self):
        s = self.core.settings
        self.server_dir.edit.setText(str(s["server_dir"] or ""))
        self.client_dir.edit.setText(str(s["client_dir"] or ""))
        self.server_py.edit.setText(str(s["server_python"] or ""))
        self.electron.edit.setText(str(s["electron"] or ""))
        self.auto_server.setChecked(bool(s["auto_start_server"]))
        self.auto_client.setChecked(bool(s["auto_start_client"]))
        self.hidden.setChecked(bool(s["start_hidden"]))
        self.show_tray.setChecked(bool(s["show_tray"]))
        self.updates.load()
        self.info.setText(f"지금 쓰는 서버 폴더: {s.server_dir or '(못 찾음)'}\n지금 쓰는 클라 폴더: {s.client_dir or '(못 찾음)'}\n"
                          "폴더를 비워 두면 런처 옆의 Project_Miku_server / Project_Miku 를 자동으로 찾아요. 설정 파일: " + s.path)

    def reset_all(self):
        ok = QMessageBox.question(
            self, "기본값으로 초기화",
            "런처 / 서버 / 클라 설정을 모두 기본값으로 되돌릴까요?\n\n"
            "· 서버 config.yaml, 클라 config.json 은 직전 파일을 .bak 으로 남겨요\n"
            "· API 키는 그대로 두어요\n"
            "· 클라가 켜져 있으면 잠깐 껐다가 다시 켜요\n"
            "· 대화 기록, 기억, 모델/페르소나 파일은 건드리지 않아요",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.info.setText("초기화하는 중… (자세한 내용은 로그 탭)")
        if not self.core.run_async("reset", self.core.reset_all_to_defaults):
            return
        QTimer.singleShot(4000, self._after_reset)   # 클라를 껐다 켜는 시간까지 기다렸다가 화면을 새로 읽는다

    def _after_reset(self):
        self.load()
        if self.on_saved:
            self.on_saved()
        if self.on_reset:
            self.on_reset()
        self.info.setText(self.info.text() + "\n기본값으로 되돌렸어요. 서버가 켜져 있었다면 서버 설정 탭 일부 항목(↻)은 서버를 다시 켜야 적용돼요.")

    def save(self):
        s = self.core.settings
        s["server_dir"] = self.server_dir.edit.text().strip()
        s["client_dir"] = self.client_dir.edit.text().strip()
        s["server_python"] = self.server_py.edit.text().strip()
        s["electron"] = self.electron.edit.text().strip()
        s["auto_start_server"] = self.auto_server.isChecked()
        s["auto_start_client"] = self.auto_client.isChecked()
        s["start_hidden"] = self.hidden.isChecked()
        s["show_tray"] = self.show_tray.isChecked()
        try:
            self.updates.store()
        except ValueError as e:
            QMessageBox.warning(self, "런처 설정", str(e))
            return
        try:
            s.save()
        except OSError as e:
            QMessageBox.warning(self, "런처 설정", f"저장하지 못했어요: {e}")
            return
        self.core.reconfigure_paths()
        if self.on_saved:
            self.on_saved()   # 트레이 표시 켜기/끄기를 바로 적용
        self.load()
        self.info.setText(self.info.text() + "\n저장했어요. 폴더/파이썬을 바꿨다면 서버·클라를 다시 켜야 적용돼요.")

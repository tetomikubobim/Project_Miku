"""런처 탭의 '업데이트' 묶음: GitHub 릴리스 확인 / 설치 / 되돌리기와 자동 업데이트 켜기·끄기."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from ..updater import parse_repo


class UpdateBox(QGroupBox):
    def __init__(self, core, parent=None):
        super().__init__("업데이트 (GitHub)", parent)
        self.core = core
        self.svc = core.updates
        self.on_restart = None   # '런처 다시 시작' 버튼이 부르는 함수 (app.py 가 연결)
        self._shown = None       # 릴리스 노트를 마지막으로 채운 버전
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("저장소"))
        self.repo = QLineEdit()
        self.repo.setPlaceholderText("owner/name  (또는 https://github.com/owner/name)")
        row.addWidget(self.repo, 1)
        lay.addLayout(row)

        self.check = QCheckBox("런처를 켤 때와 켜 두는 동안 주기적으로 새 버전 확인")
        self.auto = QCheckBox("새 버전이 있으면 런처를 켤 때 자동으로 설치 (끄면 알려 주기만 해요)")
        lay.addWidget(self.check)
        lay.addWidget(self.auto)

        self.version = QLabel("")
        self.version.setWordWrap(True)
        lay.addWidget(self.version)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: gray;")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(8)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(110)
        self.notes.setPlaceholderText("새 버전의 변경 내용이 여기에 보여요")
        self.notes.setVisible(False)
        lay.addWidget(self.notes)

        btns = QHBoxLayout()
        self.b_check = QPushButton("지금 확인")
        self.b_update = QPushButton("업데이트 설치")
        self.b_skip = QPushButton("이 버전 건너뛰기")
        self.b_rollback = QPushButton("마지막 업데이트 되돌리기")
        self.b_restart = QPushButton("런처 다시 시작")
        for b in (self.b_check, self.b_update, self.b_skip, self.b_rollback, self.b_restart):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.b_check.clicked.connect(self.do_check)
        self.b_update.clicked.connect(self.do_update)
        self.b_skip.clicked.connect(self.do_skip)
        self.b_rollback.clicked.connect(self.do_rollback)
        self.b_restart.clicked.connect(self.do_restart)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.load()

    # ---------- 설정 읽기/쓰기 ----------
    def load(self):
        s = self.core.settings
        self.repo.setText(str(s["update_repo"] or ""))
        self.check.setChecked(bool(s["update_check"]))
        self.auto.setChecked(bool(s["update_auto_apply"]))
        self.refresh()

    def store(self):
        """화면의 값을 설정에 넣는다 (파일 저장은 부르는 쪽). 저장소 주소가 이상하면 ValueError."""
        text = self.repo.text().strip()
        repo = parse_repo(text)
        if text and not repo:
            raise ValueError("업데이트 저장소는 'owner/name' 또는 https://github.com/owner/name 형식으로 적어 주세요")
        s = self.core.settings
        s["update_repo"] = repo
        s["update_check"] = self.check.isChecked()
        s["update_auto_apply"] = self.auto.isChecked()

    def _commit(self):
        try:
            self.store()
            self.core.settings.save()
        except ValueError as e:
            QMessageBox.warning(self, "업데이트", str(e))
            return False
        except OSError as e:
            QMessageBox.warning(self, "업데이트", f"설정을 저장하지 못했어요: {e}")
            return False
        self.repo.setText(str(self.core.settings["update_repo"] or ""))
        return True

    # ---------- 버튼 ----------
    def do_check(self):
        if not self._commit():
            return
        if not self.svc.repo():
            QMessageBox.information(self, "업데이트", "GitHub 저장소(owner/name)를 먼저 적어 주세요")
            return
        self.core.run_async("update", self.svc.check)

    def do_update(self):
        if not self._commit():
            return
        i = self.svc.info
        if not (i and i.available):
            self.do_check()
            return
        ok = QMessageBox.question(
            self, "업데이트 설치",
            f"{self.svc.current_version() or '지금 버전'} → {i.release.version} 으로 업데이트할까요?\n\n"
            "· 서버와 클라가 켜져 있으면 잠깐 껐다가 끝나면 다시 켜요\n"
            "· 내 설정(config.yaml, config.json), 대화 기록, 모델, persona.txt 는 건드리지 않아요\n"
            "· 바뀌는 파일은 updates/backups 에 백업하고, '되돌리기'로 복원할 수 있어요\n"
            "· 필요한 패키지가 바뀌었으면 설치 탭의 항목을 다시 설치해요",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ok == QMessageBox.StandardButton.Yes:
            self.core.run_async("update", self.svc.apply)

    def do_restart(self):
        self.svc.restart_after_swap = True   # 교체가 끝나면 새 런처를 바로 켠다
        if self.on_restart:
            self.on_restart()

    def do_skip(self):
        i = self.svc.info
        if i and i.release:
            self.core.settings["update_skip_version"] = i.release.version
            try:
                self.core.settings.save()
            except OSError:
                pass
            self.refresh()

    def do_rollback(self):
        ok = QMessageBox.question(
            self, "업데이트 되돌리기",
            "마지막 업데이트로 바뀐 파일을 백업으로 되돌릴까요?\n서버/클라가 켜져 있으면 잠깐 껐다가 다시 켜요. 내 설정과 데이터는 그대로예요.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ok == QMessageBox.StandardButton.Yes:
            self.core.run_async("update", self.svc.rollback_last)

    # ---------- 화면 갱신 ----------
    def refresh(self):
        svc, st = self.svc, self.svc.state
        busy = bool(st["busy"])
        i = svc.info
        cur = svc.current_version() or "알 수 없음"
        if i and i.release:
            self.version.setText(f"지금 버전 {cur}  ·  최신 버전 {i.release.version}" + ("  (새 버전이 있어요)" if i.available else ""))
        else:
            self.version.setText(f"지금 버전 {cur}")
        self.status.setText(st["text"] or "")
        self.bar.setVisible(busy)
        if busy:
            if st["frac"] is None:
                self.bar.setRange(0, 0)
            else:
                self.bar.setRange(0, 100)
                self.bar.setValue(int(st["frac"] * 100))
        show_notes = bool(i and i.available and i.release.body)
        self.notes.setVisible(show_notes)
        if show_notes and self._shown != i.release.version:
            self._shown = i.release.version
            self.notes.setPlainText(i.release.body)
        has_repo = bool(svc.repo())
        self.b_check.setEnabled(not busy)
        self.b_update.setEnabled(not busy and bool(i and i.available))
        self.b_skip.setEnabled(not busy and bool(i and i.available) and not svc.skipped())
        self.b_rollback.setEnabled(not busy and bool(svc.backups()))
        self.b_restart.setVisible(bool(svc.pending_swap))
        self.b_check.setToolTip("" if has_repo else "저장소를 먼저 적어 주세요")

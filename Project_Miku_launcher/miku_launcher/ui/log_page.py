"""로그 탭: 서버/클라/GPT-SoVITS/RVC/런처 로그를 한 곳에서. LLM 호출 결과([llm] 줄)만 모아 보는 필터가 있다
(LLM 연결은 실시간으로 검사하지 않고 이 로그로 확인한다)."""
import html
import os
import re

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

FILTERS = [("전체", None), ("서버", {"server"}), ("클라", {"client"}), ("LLM (호출 결과)", "llm"),
           ("GPT-SoVITS", {"sovits"}), ("RVC", {"rvc"}), ("런처", {"launcher"})]
SOURCE_LABEL = {"server": "서버", "client": "클라", "sovits": "SoVITS", "rvc": "RVC", "launcher": "런처"}
SOURCE_COLOR = {"server": "#4c8dff", "client": "#a371f7", "sovits": "#d29922", "rvc": "#3fb950", "launcher": "#8b949e"}
BAD = re.compile(r"(traceback|error|exception|오류|실패|failed|비정상)", re.I)
GOOD_LLM = re.compile(r"\[llm\]\s*성공")
MAX_BLOCKS = 5000


class LogPage(QWidget):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.seq = 0
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("보기"))
        self.filter = QComboBox()
        for name, _ in FILTERS:
            self.filter.addItem(name)
        self.filter.currentIndexChanged.connect(self.rebuild)
        bar.addWidget(self.filter)
        self.follow = QCheckBox("자동 스크롤")
        self.follow.setChecked(True)
        bar.addWidget(self.follow)
        bar.addStretch(1)
        clear = QPushButton("화면 지우기")
        clear.clicked.connect(self.clear)
        bar.addWidget(clear)
        folder = QPushButton("로그 폴더 열기")
        folder.clicked.connect(self.open_folder)
        bar.addWidget(folder)
        lay.addLayout(bar)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_BLOCKS)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(f)
        lay.addWidget(self.view, 1)

    def _match(self, entry):
        rule = FILTERS[self.filter.currentIndex()][1]
        if rule is None:
            return True
        if rule == "llm":
            return entry.is_llm
        return entry.source in rule

    @staticmethod
    def _html(e):
        import time
        ts = time.strftime("%H:%M:%S", time.localtime(e.ts))
        body = html.escape(e.text)
        if e.is_llm and GOOD_LLM.search(e.text):
            body = f'<span style="color:#3fb950">{body}</span>'
        elif BAD.search(e.text):
            body = f'<span style="color:#f85149">{body}</span>'
        src = e.source
        return (f'<span style="white-space:pre">'
                f'<span style="color:#8b949e">{ts}</span> '
                f'<span style="color:{SOURCE_COLOR.get(src, "#8b949e")}">[{SOURCE_LABEL.get(src, src)}]</span> {body}</span>')

    def _append(self, entries):
        for e in entries:
            if self._match(e):
                self.view.appendHtml(self._html(e))

    def poll(self):
        """새로 쌓인 줄을 가져와 붙인다 (화면 갱신 타이머에서 부른다)."""
        new = self.core.hub.since(self.seq)
        if not new:
            return
        self.seq = new[-1].seq
        self._append(new)
        if self.follow.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def rebuild(self, *_):
        self.view.clear()
        items = self.core.hub.since(0)
        self._append([e for e in items if self._match(e)][-MAX_BLOCKS:])
        if self.follow.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def clear(self):
        self.view.clear()

    def open_folder(self):
        d = os.path.join(self.core.base_dir, "logs")
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

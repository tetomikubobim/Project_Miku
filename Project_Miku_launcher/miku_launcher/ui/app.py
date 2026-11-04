"""런처 실행: 창 + 트레이. 창을 닫으면 트레이로 숨고, 트레이 메뉴의 '종료'(또는 창이 트레이 없이 닫힐 때)에서 서버/클라를 같이 끈다."""
import os
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from ..core import LauncherCore
from .main_window import CLIENT_RUNNING, SERVER_RUNNING, MainWindow

BUILD = "2026-11-04e"
SHUTDOWN_CAP = 45.0   # 종료 정리를 기다리는 최대 시간(초)


def make_icon(core):
    """런처 자신의 아이콘(assets/icon.ico|png)을 쓴다. 없으면 클라의 트레이 아이콘, 그것도 없으면 간단히 그린다."""
    roots = [core.settings.base_dir, getattr(sys, "_MEIPASS", "")]   # exe 옆 폴더, PyInstaller 임시 폴더
    for root in roots:
        for name in ("icon.ico", "icon.png"):
            p = os.path.join(root, "assets", name) if root else ""
            if p and os.path.isfile(p):
                return QIcon(p)
    cd = core.settings.client_dir
    if cd:
        p = os.path.join(cd, "assets", "tray.png")
        if os.path.isfile(p):
            return QIcon(p)
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.setBrush(QColor("#39c5bb"))
    q.drawEllipse(2, 2, 60, 60)
    q.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(36)
    q.setFont(f)
    q.drawText(pm.rect(), 0x84, "M")   # AlignCenter
    q.end()
    return QIcon(pm)


class LauncherApp:
    def __init__(self, base_dir, argv=None):
        self.qapp = QApplication(argv or sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)
        self.lock = QLockFile(os.path.join(tempfile.gettempdir(), "miku_launcher.lock"))
        self.lock.setStaleLockTime(0)
        self.single = self.lock.tryLock(200)
        if not self.single:
            QMessageBox.information(None, "Project Miku 런처", "런처가 이미 실행 중이에요. (트레이 아이콘을 켰다면 트레이를 확인하세요.)")
            return
        self.core = LauncherCore(base_dir)
        self.core.log(f"런처 시작 (종료 처리 개선판 {BUILD})")   # 어떤 코드로 돌고 있는지 로그에서 알 수 있게 (exe 는 다시 빌드해야 바뀐다)
        self.icon = make_icon(self.core)
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray_ok = self.tray_available and bool(self.core.settings["show_tray"])   # 트레이를 실제로 쓰는가 (끄면 창을 닫을 때 종료)
        self.window = MainWindow(self.core, self.icon, on_close_request=self.on_window_close)
        self.window.launcher_page.on_saved = self.apply_tray_setting
        self.qapp.setWindowIcon(self.icon)
        self.quitting = False
        self.tray = None
        if self.tray_ok:
            self._make_tray()
        self.core.start()
        if not (self.core.settings["start_hidden"] and self.tray_ok):
            self.window.show()

    # ---------- 트레이 ----------
    def _make_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self.qapp)
        menu = QMenu()
        c = self.core

        def act(text, fn):
            a = QAction(text, menu)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        act("창 열기", self.show_window)
        menu.addSeparator()
        self.a_srv_start = act("서버 시작", lambda: c.run_async("server", c.start_server))
        self.a_srv_stop = act("서버 중지", lambda: c.run_async("server", c.stop_server))
        act("서버 재시작", lambda: c.run_async("server", c.restart_server))
        menu.addSeparator()
        self.a_cli_start = act("클라 시작", lambda: c.run_async("client", c.start_client))
        self.a_cli_stop = act("클라 중지", lambda: c.run_async("client", c.stop_client))
        menu.addSeparator()
        act("종료 (서버/클라도 같이 꺼요)", self.request_quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()
        self.tray_timer = QTimer()
        self.tray_timer.timeout.connect(self._update_tray)
        self.tray_timer.start(1000)

    def apply_tray_setting(self):
        """런처 설정에서 '트레이 아이콘 보이기'를 바꿨을 때 바로 켜고 끈다."""
        want = self.tray_available and bool(self.core.settings["show_tray"])
        if want and self.tray is None:
            self._make_tray()
        elif not want and self.tray is not None:
            self.tray_timer.stop()
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
        self.tray_ok = want
        if not want and not self.window.isVisible():
            self.show_window()

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _update_tray(self):
        snap = self.window.last_snapshot
        if not snap or not self.tray:
            return
        sv, cl = snap["server"], snap["client"]
        s_run, c_run = sv["state"] in SERVER_RUNNING, cl["state"] in CLIENT_RUNNING
        self.a_srv_start.setEnabled(not s_run)
        self.a_srv_stop.setEnabled(s_run)
        self.a_cli_start.setEnabled(not c_run)
        self.a_cli_stop.setEnabled(c_run)
        self.tray.setToolTip(f"Project Miku\n서버: {sv['text']}\n클라: {cl['text']}\nSTT: {snap['stt']['text'] or snap['stt']['state']}\nTTS: {snap['tts']['text'] or snap['tts']['state']}")

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    # ---------- 닫기/종료 ----------
    def on_window_close(self):
        if self.tray_ok and self.tray is not None:
            self.window.hide()
            if not getattr(self, "_told", False):
                self._told = True
                self.tray.showMessage("Project Miku 런처", "트레이에서 계속 실행 중이에요. 완전히 끄려면 트레이 메뉴의 '종료'를 누르세요.")
        else:
            self.request_quit()

    def request_quit(self):
        if self.quitting:
            return
        c = self.core   # 확인 창 없이 바로 끈다 (런처가 켠 서버/클라도 같이 꺼진다)
        self.quitting = True
        self.window.setEnabled(False)
        self.window.setWindowTitle("종료하는 중…")
        self.show_window()
        t = threading.Thread(target=c.shutdown, name="shutdown", daemon=True)
        t.start()
        # 최후의 안전장치: 화면(Qt)이 멈춰서 정리/종료 처리가 돌지 못해도 일정 시간 뒤에는 프로세스를 끝낸다.
        # (런처가 켠 서버/클라는 런처가 끝나면 OS 의 작업 객체가 같이 끈다)
        wd = threading.Timer(SHUTDOWN_CAP + 10, lambda: os._exit(0))
        wd.daemon = True
        wd.start()
        self._wait_started = time.monotonic()
        self._wait = QTimer()
        self._wait.timeout.connect(lambda: self._finish(t))
        self._wait.start(200)

    def _finish(self, t):
        # 정리가 멈춰도 영원히 붙잡히지 않게 한다 (런처가 끝나면 OS 가 런처가 켠 프로세스를 같이 끈다)
        if t.is_alive() and time.monotonic() - self._wait_started < SHUTDOWN_CAP:
            return
        self._wait.stop()
        if self.tray:
            self.tray.hide()
        # Qt6 의 quit() 은 먼저 모든 창에 닫기 요청을 보내고, 창이 거절하면(우리 창은 X 를 눌러도 거절하고 직접 처리한다) 종료를 취소한다.
        # 그래서 창이 닫기를 받아들이게 한 뒤 exit() 로 이벤트 루프를 바로 끝낸다.
        self.window.allow_close = True
        self.qapp.exit(0)

    def run(self):
        if not self.single:
            return 0
        rc = self.qapp.exec()
        if self.quitting:
            # 정리는 이미 끝났다. 파이썬 종료 처리(스레드/객체 정리)에서 멈추지 않게 바로 끝낸다
            sys.stdout and sys.stdout.flush()
            os._exit(rc)
        return rc


def _set_app_id():
    """작업표시줄이 파이썬 대신 런처 아이콘으로 묶이게 한다 (Windows)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ProjectMiku.Launcher")
        except Exception:  # noqa: BLE001
            pass


def main(base_dir=None):
    _set_app_id()
    base = base_dir or os.path.dirname(os.path.abspath(sys.argv[0]))
    return LauncherApp(base).run()


if __name__ == "__main__":
    sys.exit(main())

"""Project Miku 통합 런처 (창/트레이). 더블클릭하거나 `pythonw start_launcher.pyw` 로 실행하면 cmd 창이 뜨지 않는다.

더블클릭하면 윈도우가 .pyw 에 연결된 '시스템 파이썬'으로 열어서, venv 에만 설치된 PySide6 등을 못 찾는다.
그래서 이 폴더에 venv 가 있고 지금 그 venv 의 파이썬이 아니면, venv 의 pythonw 로 다시 실행한다.
창이 안 뜨고 죽는 경우(pythonw 는 오류를 보여 주지 않는다)에는 logs/launcher_crash.log 에 원인을 남기고 메시지 상자로 알린다.
"""
import os
import subprocess
import sys
import traceback

FROZEN = getattr(sys, "frozen", False)   # PyInstaller 로 묶은 exe 로 실행 중인가
# exe 로 묶으면 __file__ 은 임시 폴더라서, 설정/로그/서버·클라 폴더 찾기는 exe 가 있는 폴더를 기준으로 한다
HERE = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REEXEC_ENV = "MIKU_LAUNCHER_REEXEC"


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _venv_python():
    for name in ("pythonw.exe", "python.exe"):
        p = os.path.join(HERE, "venv", "Scripts", name)
        if os.path.isfile(p):
            return p
    return None


def _in_launcher_venv():
    return _norm(sys.prefix) == _norm(os.path.join(HERE, "venv"))


def _message(text, title="Project Miku 런처"):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:  # noqa: BLE001
        print(text, file=sys.stderr)


def _crash(prefix):
    """오류를 파일에 남기고(pythonw 는 콘솔이 없다) 메시지 상자로 알린다."""
    tb = traceback.format_exc()
    path = os.path.join(HERE, "logs", "launcher_crash.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {sys.executable}\n{tb}\n")
    except OSError:
        path = "(로그 파일을 쓰지 못했어요)"
    last = [ln for ln in tb.strip().splitlines() if ln.strip()][-1]
    _message(f"{prefix}\n\n{last}\n\n자세한 내용: {path}")


# 1) 시스템 파이썬으로 열렸으면 venv 의 파이썬으로 다시 실행
if not FROZEN and os.name == "nt" and not os.environ.get(REEXEC_ENV) and not _in_launcher_venv():
    vpy = _venv_python()
    if vpy:
        try:
            subprocess.Popen([vpy, os.path.abspath(__file__), *sys.argv[1:]], cwd=HERE, env={**os.environ, REEXEC_ENV: "1"})
            sys.exit(0)
        except OSError:
            pass   # venv 파이썬을 못 띄우면 지금 파이썬으로 계속 시도한다

# 2) 실행. 필요한 패키지가 없으면 어떤 파이썬으로 열렸는지와 설치 명령을 알려 준다
try:
    from miku_launcher.ui.app import main
except ImportError as e:
    exe = sys.executable
    py = os.path.join(os.path.dirname(exe), "python.exe") if os.path.basename(exe).lower() == "pythonw.exe" else exe
    req = os.path.join(HERE, "requirements.txt")
    _message(f"필요한 패키지가 없어요: {e}\n\n"
             f"실행된 파이썬:\n{exe}\n\n"
             "아래 명령을 cmd 에서 그대로 실행한 뒤 다시 켜 보세요 (이 폴더의 venv 를 쓰려면 venv 를 만들고 설치):\n\n"
             f'"{py}" -m pip install -r "{req}"')
    sys.exit(1)
except Exception:  # noqa: BLE001
    _crash("런처를 불러오다 실패했어요.")
    sys.exit(1)

try:
    sys.exit(main(HERE))
except SystemExit:
    raise
except Exception:  # noqa: BLE001
    _crash("런처가 실행 중 멈췄어요.")
    sys.exit(1)

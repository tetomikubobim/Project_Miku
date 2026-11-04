"""창 없이 터미널에서 쓰는 런처: python -m miku_launcher.cli  (개발/점검용. 상태를 몇 초마다 한 줄로 찍고 로그를 그대로 보여 준다)."""
import argparse
import os
import sys
import time

from .core import LauncherCore


def main(argv=None):
    ap = argparse.ArgumentParser(description="Project Miku 런처 (터미널판)")
    ap.add_argument("--base", default=os.getcwd(), help="launcher.json 이 있는 폴더 (기본: 지금 폴더)")
    ap.add_argument("--no-server", action="store_true", help="서버를 자동으로 켜지 않는다")
    ap.add_argument("--no-client", action="store_true", help="클라를 자동으로 켜지 않는다")
    ap.add_argument("--status-every", type=float, default=5.0)
    ap.add_argument("--seconds", type=float, default=0, help="이 시간 뒤에 스스로 종료 (0 = Ctrl+C 까지)")
    a = ap.parse_args(argv)
    core = LauncherCore(a.base)
    if a.no_server:
        core.settings["auto_start_server"] = False
    if a.no_client:
        core.settings["auto_start_client"] = False
    core.start()
    seq, last_status, t0 = 0, 0.0, time.time()
    try:
        while True:
            for e in core.hub.since(seq):
                print(e.format(), flush=True)
                seq = e.seq
            if time.time() - last_status >= a.status_every:
                s = core.snapshot()
                print(f"== 서버 {s['server']['state']}({s['server']['text']}) | 클라 {s['client']['state']}({s['client']['text']}) | "
                      f"STT {s['stt']['state']} | TTS {s['tts']['state']}", flush=True)
                last_status = time.time()
            if a.seconds and time.time() - t0 > a.seconds:
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    print("== 종료하는 중…", flush=True)
    core.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

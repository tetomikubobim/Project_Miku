"""오른쪽 리소스 패널: 전체 CPU / RAM / GPU / VRAM 그래프와, 프로세스별(서버, 클라, GPT-SoVITS, RVC) 사용량 표."""
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from .widgets import GREEN, BLUE, YELLOW, RED, Sparkline

GB = 1024 ** 3
PROC_ROWS = [("server", "서버(+STT)"), ("client", "클라"), ("sovits", "GPT-SoVITS"), ("rvc", "RVC")]


def fmt_bytes(n):
    return f"{n / GB:.1f} GB" if n >= GB else f"{n / 1024 ** 2:.0f} MB"


class ResourcePanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        title = QLabel("리소스")
        title.setStyleSheet("font-weight: 600;")
        lay.addWidget(title)
        self.cpu = Sparkline("CPU", BLUE)
        self.ram = Sparkline("메모리", GREEN)
        self.gpu = Sparkline("GPU", YELLOW)
        self.vram = Sparkline("VRAM", RED)
        for w in (self.cpu, self.ram, self.gpu, self.vram):
            lay.addWidget(w)
        self.gpu_note = QLabel("")
        self.gpu_note.setWordWrap(True)
        self.gpu_note.setStyleSheet("color: gray;")
        lay.addWidget(self.gpu_note)
        sub = QLabel("프로세스별")
        sub.setStyleSheet("font-weight: 600; margin-top: 6px;")
        lay.addWidget(sub)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.cells = {}
        for c, head in enumerate(("", "CPU", "RAM")):   # 각 숫자가 무엇인지 열 제목으로 표시
            h = QLabel(head)
            h.setStyleSheet("color: gray;")
            grid.addWidget(h, 0, c)
        for r, (key, label) in enumerate(PROC_ROWS, start=1):
            name = QLabel(label)
            cpu, ram = QLabel("-"), QLabel("-")
            for w in (cpu, ram):
                w.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
            grid.addWidget(name, r, 0)
            grid.addWidget(cpu, r, 1)
            grid.addWidget(ram, r, 2)
            self.cells[key] = (cpu, ram)
        lay.addLayout(grid)
        note = QLabel("CPU: 전체 CPU 중 이 프로세스가 차지하는 비율\nRAM: 이 프로세스가 쓰는 메모리 (VRAM 은 위 그래프의 전체 값만 볼 수 있어요)")
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        self.setFixedWidth(280)

    def update_from(self, res):
        last, hist = res.get("last"), res.get("history") or {}
        if not last:
            return
        self.cpu.set_data(hist.get("cpu", []), f"{last['cpu']:.0f}%")
        self.ram.set_data(hist.get("ram", []), f"{fmt_bytes(last['ram_used'])} / {fmt_bytes(last['ram_total'])}")
        g = last.get("gpu")
        if g:
            self.gpu.set_data(hist.get("gpu", []), f"{g['util']:.0f}%")
            self.vram.set_data(hist.get("vram", []), f"{fmt_bytes(g['vram_used'])} / {fmt_bytes(g['vram_total'])}")
            self.gpu.title = f"GPU · {g['name']}"[:34]
            self.gpu_note.setText("")
        else:
            self.gpu.set_data([], "-")
            self.vram.set_data([], "-")
            self.gpu_note.setText(last.get("gpu_error") or "")
        for key, _ in PROC_ROWS:
            cpu, ram = self.cells[key]
            g = last["procs"].get(key) or {}
            if g.get("n"):
                cpu.setText(f"{g['cpu']:4.1f}%")
                ram.setText(fmt_bytes(g["rss"]))
            else:
                cpu.setText("  -  ")
                ram.setText("-")

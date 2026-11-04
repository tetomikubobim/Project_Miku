"""작은 그리기 위젯들: 상태 불빛, 사용량 그래프."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

GREEN, YELLOW, RED, GRAY, BLUE = "#2ea043", "#d29922", "#f85149", "#8b949e", "#58a6ff"
STATE_COLORS = {
    "ok": GREEN,
    "loading": YELLOW, "starting": YELLOW, "stopping": YELLOW, "warn": YELLOW,
    "error": RED, "down": RED,
    "idle": BLUE,
    "stopped": GRAY, "off": GRAY, "unknown": GRAY,
}


class StatusLight(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "unknown"
        self.setFixedSize(18, 18)

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(STATE_COLORS.get(self.state, GRAY))
        glow = QColor(c)
        glow.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(0.5, 0.5, 17, 17))
        p.setBrush(QBrush(c))
        p.drawEllipse(QRectF(4, 4, 10, 10))


class Sparkline(QWidget):
    """최근 값(0~100)을 선 그래프로 그리고, 왼쪽 위에 이름, 오른쪽 위에 현재 값 글자를 얹는다."""

    def __init__(self, title, color, parent=None):
        super().__init__(parent)
        self.title, self.color = title, color
        self.values = []
        self.caption = "-"
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, values, caption):
        self.values, self.caption = list(values), caption
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = self.palette().color(QPalette.ColorRole.Base)
        edge = self.palette().color(QPalette.ColorRole.Mid)
        p.setPen(QPen(edge, 1))
        p.setBrush(QBrush(base))
        p.drawRoundedRect(r, 6, 6)
        vals = self.values
        if len(vals) >= 2:
            inner = r.adjusted(4, 18, -4, -4)
            n = len(vals)
            pts = [QPointF(inner.left() + inner.width() * i / (n - 1), inner.bottom() - inner.height() * max(0.0, min(100.0, v)) / 100.0) for i, v in enumerate(vals)]
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            fill = QPainterPath(path)
            fill.lineTo(QPointF(inner.right(), inner.bottom()))
            fill.lineTo(QPointF(inner.left(), inner.bottom()))
            fill.closeSubpath()
            c = QColor(self.color)
            fc = QColor(c)
            fc.setAlpha(45)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fc))
            p.drawPath(fill)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(c, 1.6))
            p.drawPath(path)
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        p.setFont(f)
        text = self.palette().color(QPalette.ColorRole.Text)
        p.setPen(text)
        p.drawText(QRectF(r.left() + 8, r.top() + 2, r.width() / 2, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        p.drawText(QRectF(r.left() + r.width() / 2, r.top() + 2, r.width() / 2 - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.caption)

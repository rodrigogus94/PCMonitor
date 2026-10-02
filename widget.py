"""Widget flutuante: janela sem borda, sempre no topo, arrastável, com cantos magnéticos."""
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from sensors import format_value

C_OK, C_WARN, C_BAD = QColor("#4ade80"), QColor("#facc15"), QColor("#f87171")
C_TEXT, C_MUTED = QColor("#e8eaf0"), QColor("#9aa3b5")


class MonitorWidget(QWidget):
    moved = Signal(str, int, int)
    open_settings = Signal(str)
    hide_requested = Signal(str)
    lock_toggled = Signal(str, bool)

    def __init__(self, wcfg, app_cfg, catalog):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.wcfg, self.app_cfg, self.catalog = wcfg, app_cfg, catalog
        self.values = {}
        self._rows = []
        self._wmax = 0
        self._drag = None
        self._ct_applied = None
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle(wcfg.get("name", "PC Monitor"))
        self.apply_config(first=True)

    # ------------------------------------------------------------------ config / dados
    def apply_config(self, first=False):
        self._wmax = 0
        want_ct = bool(self.wcfg.get("click_through"))
        if self._ct_applied is not None and want_ct != self._ct_applied:
            was_visible = self.isVisible()
            self.setWindowFlag(Qt.WindowTransparentForInput, want_ct)
            if was_visible:
                self.show()
        elif first and want_ct:
            self.setWindowFlag(Qt.WindowTransparentForInput, True)
        self._ct_applied = want_ct
        self.setWindowTitle(self.wcfg.get("name", "PC Monitor"))
        self.set_values(self.values)
        if first:
            self.move(int(self.wcfg.get("x", 40)), int(self.wcfg.get("y", 40)))
            self.ensure_on_screen()

    def set_values(self, values):
        self.values = values
        s = float(self.wcfg.get("scale", 1.0))
        self._f_label = self._font(9.5 * s)
        self._f_value = self._font(10.5 * s, bold=True)
        self._f_title = self._font(8.5 * s, bold=True)
        fl, fv = QFontMetrics(self._f_label), QFontMetrics(self._f_value)
        self._fm = (fl, fv, QFontMetrics(self._f_title))
        rows = []
        for mid in self.wcfg.get("metrics", []):
            m = self.catalog.get(mid)
            label = m.short if m else mid
            v = values.get(mid)
            text = format_value(m, v)
            color = self._color_for(m, v)
            ratio = None
            if m and not m.text and m.vmax and v is not None:
                ratio = max(0.0, min(1.0, float(v) / m.vmax))
            rows.append((label, text, color, ratio))
        self._rows = rows
        self._measure()
        self.update()

    @staticmethod
    def _font(pt, bold=False):
        f = QFont("Segoe UI")
        f.setPointSizeF(max(5.0, pt))
        f.setBold(bold)
        return f

    def _color_for(self, m, v):
        if m is None or v is None or not m.alert or m.text:
            return C_TEXT
        key = "temp" if m.unit == "°C" else "pct" if m.unit == "%" else None
        if not key:
            return C_TEXT
        warn, bad = self.app_cfg["thresholds"][key]
        return C_BAD if v >= bad else C_WARN if v >= warn else C_OK

    # ------------------------------------------------------------------ medidas
    def _geom(self):
        s = float(self.wcfg.get("scale", 1.0))
        return dict(pad=int(10 * s), gap=int(16 * s), bar_h=max(2, int(3 * s)), rad=12 * s, s=s)

    def _measure(self):
        g = self._geom()
        fl, fv, ft = self._fm
        pad, gap, bar_h = g["pad"], g["gap"], g["bar_h"]
        bars = bool(self.wcfg.get("bars", True))
        title = bool(self.wcfg.get("title", True))
        maxlabel = int(170 * g["s"])
        if self.wcfg.get("layout") == "horizontal":
            col_ws = []
            for label, text, _c, _r in self._rows:
                lw = min(fl.horizontalAdvance(label), maxlabel)
                col_ws.append(max(lw, fv.horizontalAdvance(text)) + 4)
            w = pad * 2 + sum(col_ws) + gap * max(0, len(col_ws) - 1)
            h = pad * 2 + (ft.height() + 4 if title else 0) + fl.height() + fv.height() + (bar_h + 4 if bars else 2)
            self._col_ws = col_ws
        else:
            lw = max([min(fl.horizontalAdvance(r[0]), maxlabel) for r in self._rows] or [0])
            vw = max([fv.horizontalAdvance(r[1]) for r in self._rows] or [0])
            w = pad * 2 + lw + gap + vw
            row_h = max(fl.height(), fv.height()) + (bar_h + 5 if bars else 3)
            h = pad * 2 + (ft.height() + 5 if title else 0) + row_h * max(1, len(self._rows)) - 2
            self._lw = lw
        if title:
            w = max(w, pad * 2 + ft.horizontalAdvance(self.wcfg.get("name", "")) + 8)
        w = max(w, int(120 * g["s"]))
        self._wmax = max(self._wmax, w)           # só cresce: evita tremedeira quando os números mudam
        if self.width() != self._wmax or self.height() != h:
            self.setFixedSize(self._wmax, int(h))

    # ------------------------------------------------------------------ desenho
    def paintEvent(self, _e):
        g = self._geom()
        fl, fv, ft = self._fm
        pad, bar_h = g["pad"], g["bar_h"]
        bars = bool(self.wcfg.get("bars", True))
        title = bool(self.wcfg.get("title", True))
        accent = QColor(self.wcfg.get("accent", "#4cc9f0"))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        bg = QColor(16, 18, 24)
        bg.setAlphaF(max(0.2, min(1.0, float(self.wcfg.get("opacity", 0.88)))))
        p.setBrush(bg)
        p.setPen(QPen(QColor(255, 255, 255, 38), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), g["rad"], g["rad"])
        y = pad
        if title:
            p.setFont(self._f_title)
            p.setPen(accent)
            p.drawText(pad, y + ft.ascent(), self.wcfg.get("name", "").upper())
            y += ft.height() + 2
            p.setPen(QPen(accent, 1))
            p.drawLine(pad, y, self.width() - pad, y)
            y += 3
        if self.wcfg.get("layout") == "horizontal":
            x = pad
            for (label, text, color, ratio), cw in zip(self._rows, self._col_ws):
                p.setFont(self._f_label)
                p.setPen(C_MUTED)
                p.drawText(x, y + fl.ascent(), fl.elidedText(label, Qt.ElideRight, cw))
                p.setFont(self._f_value)
                p.setPen(color)
                p.drawText(x, y + fl.height() + fv.ascent(), text)
                if bars and ratio is not None:
                    self._bar(p, x, y + fl.height() + fv.height() + 2, cw - 4, bar_h, ratio, color)
                x += cw + g["gap"]
        else:
            row_h = max(fl.height(), fv.height()) + (bar_h + 5 if bars else 3)
            inner = self.width() - pad * 2
            for label, text, color, ratio in self._rows:
                p.setFont(self._f_label)
                p.setPen(C_MUTED)
                p.drawText(pad, y + fl.ascent(), fl.elidedText(label, Qt.ElideRight, self._lw))
                p.setFont(self._f_value)
                p.setPen(color)
                p.drawText(self.width() - pad - fv.horizontalAdvance(text), y + fv.ascent(), text)
                if bars and ratio is not None:
                    self._bar(p, pad, y + max(fl.height(), fv.height()) + 1, inner, bar_h, ratio, color)
                y += row_h

    @staticmethod
    def _bar(p, x, y, w, h, ratio, color):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        if ratio > 0:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y, max(h, w * ratio), h), h / 2, h / 2)

    # ------------------------------------------------------------------ mouse / posição
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.wcfg.get("locked"):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(self._snap(e.globalPosition().toPoint() - self._drag))

    def mouseReleaseEvent(self, e):
        if self._drag is not None and e.button() == Qt.LeftButton:
            self._drag = None
            self.moved.emit(self.wcfg["id"], self.x(), self.y())

    def mouseDoubleClickEvent(self, _e):
        self.open_settings.emit(self.wcfg["id"])

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("Abrir configurações", lambda: self.open_settings.emit(self.wcfg["id"]))
        locked = bool(self.wcfg.get("locked"))
        act = menu.addAction("Travar posição")
        act.setCheckable(True)
        act.setChecked(locked)
        act.toggled.connect(lambda c: self.lock_toggled.emit(self.wcfg["id"], c))
        menu.addSeparator()
        menu.addAction("Ocultar este widget", lambda: self.hide_requested.emit(self.wcfg["id"]))
        menu.exec(e.globalPos())

    def _snap(self, pos, margin=14):
        screen = QGuiApplication.screenAt(pos + self.rect().center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        if abs(x - geo.left()) < margin:
            x = geo.left()
        elif abs(x + w - (geo.right() + 1)) < margin:
            x = geo.right() + 1 - w
        if abs(y - geo.top()) < margin:
            y = geo.top()
        elif abs(y + h - (geo.bottom() + 1)) < margin:
            y = geo.bottom() + 1 - h
        pos.setX(x)
        pos.setY(y)
        return pos

    def ensure_on_screen(self):
        center = self.frameGeometry().center()
        if not any(s.geometry().contains(center) for s in QGuiApplication.screens()):
            geo = QGuiApplication.primaryScreen().availableGeometry()
            self.move(geo.left() + 40, geo.top() + 40)
            self.moved.emit(self.wcfg["id"], self.x(), self.y())

    def place_in_corner(self, screen_index, corner, margin=16):
        screens = QGuiApplication.screens()
        screen = screens[screen_index] if 0 <= screen_index < len(screens) else QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.left() + margin if "l" in corner else geo.right() + 1 - self.width() - margin
        y = geo.top() + margin if "t" in corner else geo.bottom() + 1 - self.height() - margin
        self.move(x, y)
        self.moved.emit(self.wcfg["id"], x, y)

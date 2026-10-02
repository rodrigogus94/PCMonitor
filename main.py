"""PC Monitor — monitor em tempo real com widgets flutuantes.  Execute:  python main.py"""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QLockFile, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

import config
import history
from sensors import Collector, format_value, is_admin
from settings_window import SettingsWindow
from widget import MonitorWidget


def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1b1f2a"))
    p.setPen(QColor("#4cc9f0"))
    p.drawRoundedRect(3, 3, 58, 58, 12, 12)
    p.setPen(Qt.NoPen)
    for i, (h, col) in enumerate(((22, "#4ade80"), (36, "#4cc9f0"), (28, "#facc15"))):
        p.setBrush(QColor(col))
        p.drawRoundedRect(13 + i * 14, 52 - h, 9, h, 3, 3)
    p.end()
    return QIcon(pm)


def apply_dark(app):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#1b1e27"))
    pal.setColor(QPalette.WindowText, QColor("#e8eaf0"))
    pal.setColor(QPalette.Base, QColor("#12141b"))
    pal.setColor(QPalette.AlternateBase, QColor("#1b1e27"))
    pal.setColor(QPalette.Text, QColor("#e8eaf0"))
    pal.setColor(QPalette.Button, QColor("#272b38"))
    pal.setColor(QPalette.ButtonText, QColor("#e8eaf0"))
    pal.setColor(QPalette.ToolTipBase, QColor("#272b38"))
    pal.setColor(QPalette.ToolTipText, QColor("#e8eaf0"))
    pal.setColor(QPalette.Highlight, QColor("#2f6f9f"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor("#7f8799"))
    app.setPalette(pal)


class SensorThread(QThread):
    catalog_ready = Signal(object, object)   # catálogo, status
    sampled = Signal(object)
    failed = Signal(str)

    def __init__(self, interval_getter):
        super().__init__()
        self._interval = interval_getter
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            col = Collector()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return
        self.catalog_ready.emit(col.catalog, {"lhm_error": col.lhm_error, "nv_error": col.nv_error})
        self.msleep(400)
        while not self._stop:
            t0 = time.monotonic()
            try:
                vals = col.sample()
            except Exception:  # noqa: BLE001
                vals = {}
            self.sampled.emit(vals)
            wait = int(self._interval()) - int((time.monotonic() - t0) * 1000)
            self.msleep(max(50, wait))
        col.close()


class Controller(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = config.load()
        self.catalog = {}
        self.status = {}
        self.values = {}
        self.widgets = {}
        self.settings = None
        self.logger = history.HistoryLogger(config.history_dir())
        self._last_log = 0.0
        self._alert_t = {}
        self._warned_lhm = False
        self._closed = False

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(lambda: config.save(self.cfg))

        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("PC Monitor")
        self.tray.activated.connect(lambda r: self.open_settings() if r == QSystemTrayIcon.DoubleClick else None)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self._build_menu)
        self.tray.show()

        self.sync_widgets()
        self.thread = SensorThread(lambda: self.cfg["interval_ms"])
        self.thread.catalog_ready.connect(self.on_catalog)
        self.thread.sampled.connect(self.on_sample)
        self.thread.failed.connect(lambda m: self.tray.showMessage("PC Monitor", f"Falha ao iniciar sensores: {m}",
                                                                   QSystemTrayIcon.Critical, 10000))
        self.thread.start()
        app.aboutToQuit.connect(self.shutdown)

    # ------------------------------------------------------------ bandeja
    def _build_menu(self):
        m = self.menu
        m.clear()
        m.addAction("Abrir configurações", self.open_settings)
        m.addSeparator()
        for w in self.cfg["widgets"]:
            a = QAction(f"Mostrar: {w['name']}", m)
            a.setCheckable(True)
            a.setChecked(bool(w.get("visible", True)))
            a.toggled.connect(lambda c, wid=w["id"]: self.set_visible(wid, c))
            m.addAction(a)
        m.addSeparator()
        m.addAction("Sair", self.quit)

    def set_visible(self, wid, visible):
        for w in self.cfg["widgets"]:
            if w["id"] == wid:
                w["visible"] = bool(visible)
        self.sync_widgets()
        self.queue_save()
        if self.settings:
            self.settings.rebuild_all()

    def queue_save(self):
        self.save_timer.start()

    # ------------------------------------------------------------ widgets
    def sync_widgets(self):
        ids = {w["id"] for w in self.cfg["widgets"]}
        for wid in list(self.widgets):
            if wid not in ids:
                self.widgets.pop(wid).close()
        for wc in self.cfg["widgets"]:
            mw = self.widgets.get(wc["id"])
            if mw is None:
                mw = MonitorWidget(wc, self.cfg, self.catalog)
                mw.moved.connect(self.on_moved)
                mw.open_settings.connect(self.open_settings)
                mw.hide_requested.connect(lambda wid: self.set_visible(wid, False))
                mw.lock_toggled.connect(self.on_lock)
                self.widgets[wc["id"]] = mw
            else:
                mw.wcfg = wc
                mw.apply_config()
            mw.set_values(self.values)
            if wc.get("visible", True):
                mw.show()
            else:
                mw.hide()

    def on_moved(self, wid, x, y):
        for w in self.cfg["widgets"]:
            if w["id"] == wid:
                w["x"], w["y"] = x, y
        self.queue_save()

    def on_lock(self, wid, locked):
        for w in self.cfg["widgets"]:
            if w["id"] == wid:
                w["locked"] = bool(locked)
        self.queue_save()
        if self.settings:
            self.settings.rebuild_all()

    def on_place(self, wid, screen_index, corner):
        mw = self.widgets.get(wid)
        if mw:
            mw.show()
            mw.place_in_corner(screen_index, corner)

    # ------------------------------------------------------------ configurações
    def open_settings(self, widget_id=None):
        if self.settings is None:
            self.settings = SettingsWindow(self.cfg, self.catalog, self.status, config.history_dir(),
                                           lambda: self.logger.path if self.logger.active else None)
            self.settings.changed.connect(self.on_cfg_changed)
            self.settings.structure_changed.connect(self.on_cfg_changed)
            self.settings.place_requested.connect(self.on_place)
            self.settings.logging_changed.connect(self.apply_logging)
        if widget_id:
            self.settings.select_widget(widget_id)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def on_cfg_changed(self):
        self.sync_widgets()
        self.queue_save()

    # ------------------------------------------------------------ dados
    def on_catalog(self, catalog, status):
        self.catalog.clear()
        self.catalog.update(catalog)
        self.status = status
        if not self._warned_lhm and status.get("lhm_error"):
            self._warned_lhm = True
            self.tray.showMessage("PC Monitor", "Temperaturas da CPU indisponíveis: " + status["lhm_error"],
                                  QSystemTrayIcon.Warning, 12000)
        # sessão anterior terminou sem fechar o monitor?
        prev = history.find_unclean(config.history_dir())
        if prev and self.cfg["logging"]["enabled"]:
            try:
                s = history.summarize(prev["path"])
                when = prev["start"].strftime("%d/%m %H:%M") if prev["start"] else "?"
                self.tray.showMessage(
                    "PC Monitor — queda detectada",
                    f"A sessão de {when} terminou sem o monitor ser fechado (queda, reinício ou encerrado à força). "
                    f"Último registro às {prev['last']}: {history.describe_last(s)}. Veja a aba Histórico.",
                    QSystemTrayIcon.Warning, 20000)
            except OSError:
                pass
        self.apply_logging()
        if self.settings:
            self.settings.set_catalog_ready(status)
        self.sync_widgets()

    def apply_logging(self):
        want = bool(self.cfg["logging"]["enabled"]) and bool(self.catalog)
        if want and not self.logger.active:
            history.cleanup(config.history_dir(), int(self.cfg["logging"]["retention_days"]))
            self.logger.start(self.catalog)
            self._last_log = 0.0
        elif not want and self.logger.active:
            self.logger.close()

    def on_sample(self, values):
        self.values = values
        for mw in self.widgets.values():
            mw.set_values(values)
        if self.settings:
            self.settings.update_live(values)
        now = time.monotonic()
        if self.logger.active and now - self._last_log >= int(self.cfg["logging"]["interval_s"]):
            self._last_log = now
            self.logger.write(values)
        self.check_alerts(values, now)

    def check_alerts(self, values, now):
        a = self.cfg["alerts"]
        if not a["enabled"]:
            return
        checks = []
        for mid, m in self.catalog.items():
            if m.unit == "°C" and (mid == "cpu.temp" or mid.endswith(".temp")):
                v = values.get(mid)
                if v is not None and v >= a["temp_c"]:
                    checks.append((mid, f"{m.label} em {v:.0f} °C (limite {a['temp_c']} °C)"))
        v = values.get("ram.pct")
        if v is not None and v >= a["ram_pct"]:
            checks.append(("ram.pct", f"RAM em {v:.0f}% (limite {a['ram_pct']}%)"))
        for key, msg in checks:
            if now - self._alert_t.get(key, -1e9) >= a["cooldown_s"]:
                self._alert_t[key] = now
                self.tray.showMessage("PC Monitor — alerta", msg, QSystemTrayIcon.Warning, 8000)

    # ------------------------------------------------------------ saída
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.thread.stop()
            self.thread.wait(3000)
        except Exception:  # noqa: BLE001
            pass
        self.logger.close()
        config.save(self.cfg)

    def quit(self):
        self.tray.hide()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PC Monitor")
    app.setQuitOnLastWindowClosed(False)
    apply_dark(app)
    lock = QLockFile(os.path.join(config.data_dir(), "pcmonitor.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        QMessageBox.information(None, "PC Monitor", "O PC Monitor já está em execução (veja o ícone na bandeja).")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "PC Monitor", "A bandeja do sistema não está disponível.")
    ctl = Controller(app)  # noqa: F841
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())

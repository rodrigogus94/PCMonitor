"""Janela de configurações: widgets, leituras ao vivo, ajustes gerais e histórico."""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget, QMessageBox,
)

import autostart
import config
import history
from sensors import format_value, is_admin


def _spin(lo, hi, step=1, suffix=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsWindow(QWidget):
    changed = Signal()            # qualquer ajuste (aplicar + salvar)
    structure_changed = Signal()  # widget criado/removido
    place_requested = Signal(str, int, str)   # widget_id, índice da tela, canto ("tl","tr","bl","br")
    logging_changed = Signal()

    def __init__(self, cfg, catalog, status, history_folder):
        super().__init__()
        self.cfg, self.catalog, self.status = cfg, catalog, status
        self.history_folder = history_folder
        self._loading = False
        self._live_rows = {}
        self.setWindowTitle("PC Monitor — Configurações")
        self.resize(980, 720)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.tabs.addTab(self._build_widgets_tab(), "Widgets")
        self.tabs.addTab(self._build_live_tab(), "Ao vivo")
        self.tabs.addTab(self._build_general_tab(), "Geral")
        self.tabs.addTab(self._build_history_tab(), "Histórico")
        self.tabs.currentChanged.connect(self._on_tab)
        self.rebuild_all()

    # ============================================================== aba Widgets
    def _build_widgets_tab(self):
        w = QWidget()
        lay = QHBoxLayout(w)

        left = QVBoxLayout()
        self.list_widgets = QListWidget()
        self.list_widgets.currentRowChanged.connect(self._load_widget)
        left.addWidget(self.list_widgets, 1)
        for text, fn in (("Novo widget", self._add_widget), ("Duplicar", self._dup_widget),
                         ("Remover", self._remove_widget)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            left.addWidget(b)
        lay.addLayout(left, 1)

        right = QVBoxLayout()
        look = QGroupBox("Aparência")
        form = QFormLayout(look)
        self.ed_name = QLineEdit()
        self.ed_name.textEdited.connect(lambda t: self._set("name", t, name=True))
        form.addRow("Nome", self.ed_name)
        self.cb_visible = QCheckBox("Mostrar na tela")
        self.cb_visible.toggled.connect(lambda c: self._set("visible", c))
        form.addRow("", self.cb_visible)
        op = QHBoxLayout()
        self.sl_opacity = QSlider(Qt.Horizontal)
        self.sl_opacity.setRange(20, 100)
        self.lb_opacity = QLabel("88%")
        self.sl_opacity.valueChanged.connect(self._on_opacity)
        op.addWidget(self.sl_opacity, 1)
        op.addWidget(self.lb_opacity)
        form.addRow("Opacidade do fundo", op)
        self.sp_scale = QDoubleSpinBox()
        self.sp_scale.setRange(0.6, 2.5)
        self.sp_scale.setSingleStep(0.1)
        self.sp_scale.valueChanged.connect(lambda v: self._set("scale", round(v, 2)))
        form.addRow("Tamanho (escala)", self.sp_scale)
        self.cmb_layout = QComboBox()
        self.cmb_layout.addItem("Vertical (lista)", "vertical")
        self.cmb_layout.addItem("Horizontal (barra)", "horizontal")
        self.cmb_layout.currentIndexChanged.connect(lambda _i: self._set("layout", self.cmb_layout.currentData()))
        form.addRow("Layout", self.cmb_layout)
        flags = QHBoxLayout()
        self.cb_bars = QCheckBox("Barras")
        self.cb_title = QCheckBox("Título")
        self.cb_locked = QCheckBox("Travar posição")
        self.cb_ct = QCheckBox("Clique atravessa")
        self.cb_ct.setToolTip("O widget deixa de receber cliques (o mouse passa para a janela de baixo). "
                              "Para desfazer, desmarque aqui.")
        for cb, key in ((self.cb_bars, "bars"), (self.cb_title, "title"),
                        (self.cb_locked, "locked"), (self.cb_ct, "click_through")):
            cb.toggled.connect(lambda c, k=key: self._set(k, c))
            flags.addWidget(cb)
        form.addRow("Opções", flags)
        self.btn_accent = QPushButton()
        self.btn_accent.clicked.connect(self._pick_accent)
        form.addRow("Cor de destaque", self.btn_accent)
        right.addWidget(look)

        pos = QGroupBox("Posição (cantos das telas)")
        pl = QHBoxLayout(pos)
        self.cmb_screen = QComboBox()
        pl.addWidget(QLabel("Tela:"))
        pl.addWidget(self.cmb_screen, 1)
        for text, corner in (("↖", "tl"), ("↗", "tr"), ("↙", "bl"), ("↘", "br")):
            b = QPushButton(text)
            b.setFixedWidth(40)
            b.clicked.connect(lambda _c=False, c=corner: self._place(c))
            pl.addWidget(b)
        pl.addWidget(QLabel("ou arraste o widget na tela"))
        right.addWidget(pos)

        met = QGroupBox("Informações exibidas")
        ml = QHBoxLayout(met)
        lv = QVBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Buscar métrica (ex.: temp, gpu, disco)…")
        self.ed_search.textChanged.connect(self._filter_tree)
        lv.addWidget(self.ed_search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self._tree_changed)
        lv.addWidget(self.tree, 1)
        ml.addLayout(lv, 3)
        rv = QVBoxLayout()
        rv.addWidget(QLabel("Ordem no widget:"))
        self.list_sel = QListWidget()
        rv.addWidget(self.list_sel, 1)
        ud = QHBoxLayout()
        for text, d in (("▲ Subir", -1), ("▼ Descer", 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, dd=d: self._move_metric(dd))
            ud.addWidget(b)
        rv.addLayout(ud)
        ml.addLayout(rv, 2)
        right.addWidget(met, 1)
        lay.addLayout(right, 3)
        return w

    def _wcfg(self):
        row = self.list_widgets.currentRow()
        ws = self.cfg["widgets"]
        return ws[row] if 0 <= row < len(ws) else None

    def _set(self, key, value, name=False):
        w = self._wcfg()
        if self._loading or w is None:
            return
        w[key] = value
        if name:
            self.list_widgets.currentItem().setText(value or "(sem nome)")
        self.changed.emit()

    def _on_opacity(self, v):
        self.lb_opacity.setText(f"{v}%")
        self._set("opacity", v / 100.0)

    def _pick_accent(self):
        w = self._wcfg()
        if not w:
            return
        c = QColorDialog.getColor(QColor(w.get("accent", "#4cc9f0")), self, "Cor de destaque")
        if c.isValid():
            self._set("accent", c.name())
            self._paint_accent(c.name())

    def _paint_accent(self, hex_):
        self.btn_accent.setText(hex_)
        self.btn_accent.setStyleSheet(f"background:{hex_}; color:#111; font-weight:bold;")

    def _place(self, corner):
        w = self._wcfg()
        if w:
            self.place_requested.emit(w["id"], max(0, self.cmb_screen.currentIndex()), corner)

    def _add_widget(self):
        self.cfg["widgets"].append(config.new_widget(f"Widget {len(self.cfg['widgets']) + 1}"))
        self._fill_list(len(self.cfg["widgets"]) - 1)
        self.structure_changed.emit()

    def _dup_widget(self):
        w = self._wcfg()
        if not w:
            return
        import copy
        n = copy.deepcopy(w)
        n["id"] = config.new_widget()["id"]
        n["name"] = w["name"] + " (cópia)"
        n["x"], n["y"] = w.get("x", 40) + 30, w.get("y", 40) + 30
        self.cfg["widgets"].append(n)
        self._fill_list(len(self.cfg["widgets"]) - 1)
        self.structure_changed.emit()

    def _remove_widget(self):
        if len(self.cfg["widgets"]) <= 1:
            QMessageBox.information(self, "PC Monitor", "Mantenha pelo menos um widget (você pode ocultá-lo).")
            return
        row = self.list_widgets.currentRow()
        if row >= 0:
            del self.cfg["widgets"][row]
            self._fill_list(max(0, row - 1))
            self.structure_changed.emit()

    def _fill_list(self, select=0):
        self.list_widgets.blockSignals(True)
        self.list_widgets.clear()
        for w in self.cfg["widgets"]:
            self.list_widgets.addItem(QListWidgetItem(w.get("name") or "(sem nome)"))
        self.list_widgets.blockSignals(False)
        self.list_widgets.setCurrentRow(select)
        self._load_widget(select)

    def select_widget(self, widget_id):
        for i, w in enumerate(self.cfg["widgets"]):
            if w["id"] == widget_id:
                self.list_widgets.setCurrentRow(i)
                self.tabs.setCurrentIndex(0)
                return

    def _load_widget(self, _row=None):
        w = self._wcfg()
        if w is None:
            return
        self._loading = True
        self.ed_name.setText(w.get("name", ""))
        self.cb_visible.setChecked(bool(w.get("visible", True)))
        self.sl_opacity.setValue(int(round(float(w.get("opacity", 0.88)) * 100)))
        self.lb_opacity.setText(f"{self.sl_opacity.value()}%")
        self.sp_scale.setValue(float(w.get("scale", 1.0)))
        self.cmb_layout.setCurrentIndex(max(0, self.cmb_layout.findData(w.get("layout", "vertical"))))
        self.cb_bars.setChecked(bool(w.get("bars", True)))
        self.cb_title.setChecked(bool(w.get("title", True)))
        self.cb_locked.setChecked(bool(w.get("locked", False)))
        self.cb_ct.setChecked(bool(w.get("click_through", False)))
        self._paint_accent(w.get("accent", "#4cc9f0"))
        self._sync_tree_checks()
        self._loading = False
        self._refresh_selected()

    # ---- árvore de métricas
    def _rebuild_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {}
        for mid, m in sorted(self.catalog.items(), key=lambda kv: (kv[1].group, kv[1].label)):
            top = groups.get(m.group)
            if top is None:
                top = QTreeWidgetItem([m.group])
                top.setFlags(Qt.ItemIsEnabled)
                groups[m.group] = top
                self.tree.addTopLevelItem(top)
            it = QTreeWidgetItem([m.label])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Unchecked)
            it.setData(0, Qt.UserRole, mid)
            top.addChild(it)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._sync_tree_checks()

    def _iter_items(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                yield top.child(j)

    def _sync_tree_checks(self):
        w = self._wcfg()
        chosen = set(w.get("metrics", [])) if w else set()
        self.tree.blockSignals(True)
        for it in self._iter_items():
            it.setCheckState(0, Qt.Checked if it.data(0, Qt.UserRole) in chosen else Qt.Unchecked)
        self.tree.blockSignals(False)

    def _tree_changed(self, item, _col):
        w = self._wcfg()
        if self._loading or w is None:
            return
        mid = item.data(0, Qt.UserRole)
        if item.checkState(0) == Qt.Checked:
            if mid not in w["metrics"]:
                w["metrics"].append(mid)
        elif mid in w["metrics"]:
            w["metrics"].remove(mid)
        self._refresh_selected()
        self.changed.emit()

    def _filter_tree(self, text):
        t = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            vis = 0
            for j in range(top.childCount()):
                ch = top.child(j)
                hide = bool(t) and t not in ch.text(0).lower() and t not in top.text(0).lower()
                ch.setHidden(hide)
                vis += 0 if hide else 1
            top.setHidden(vis == 0)

    def _refresh_selected(self, keep=None):
        w = self._wcfg()
        self.list_sel.clear()
        if not w:
            return
        for mid in w["metrics"]:
            m = self.catalog.get(mid)
            self.list_sel.addItem(m.label if m else f"{mid} (indisponível)")
        if keep is not None and 0 <= keep < self.list_sel.count():
            self.list_sel.setCurrentRow(keep)

    def _move_metric(self, d):
        w = self._wcfg()
        row = self.list_sel.currentRow()
        if not w or row < 0:
            return
        new = row + d
        if 0 <= new < len(w["metrics"]):
            w["metrics"][row], w["metrics"][new] = w["metrics"][new], w["metrics"][row]
            self._refresh_selected(keep=new)
            self.changed.emit()

    # ============================================================== aba Ao vivo
    def _build_live_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.ed_live = QLineEdit()
        self.ed_live.setPlaceholderText("Filtrar…")
        self.ed_live.textChanged.connect(self._filter_live)
        lay.addWidget(self.ed_live)
        self.tbl_live = QTableWidget(0, 3)
        self.tbl_live.setHorizontalHeaderLabels(["Grupo", "Métrica", "Valor"])
        self.tbl_live.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_live.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_live.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl_live)
        return w

    def _rebuild_live(self):
        self.tbl_live.setRowCount(0)
        self._live_rows = {}
        items = sorted(self.catalog.items(), key=lambda kv: (kv[1].group, kv[1].label))
        self.tbl_live.setRowCount(len(items))
        for r, (mid, m) in enumerate(items):
            self.tbl_live.setItem(r, 0, QTableWidgetItem(m.group))
            self.tbl_live.setItem(r, 1, QTableWidgetItem(m.label))
            self.tbl_live.setItem(r, 2, QTableWidgetItem("—"))
            self._live_rows[mid] = r

    def _filter_live(self, text):
        t = text.strip().lower()
        for r in range(self.tbl_live.rowCount()):
            hay = (self.tbl_live.item(r, 0).text() + " " + self.tbl_live.item(r, 1).text()).lower()
            self.tbl_live.setRowHidden(r, bool(t) and t not in hay)

    def update_live(self, values):
        if not self.isVisible() or self.tabs.currentIndex() != 1:
            return
        for mid, r in self._live_rows.items():
            self.tbl_live.item(r, 2).setText(format_value(self.catalog.get(mid), values.get(mid)))

    # ============================================================== aba Geral
    def _build_general_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        g1 = QGroupBox("Atualização e limites de cor")
        f1 = QFormLayout(g1)
        self.sp_interval = _spin(250, 10000, 250, " ms")
        self.sp_interval.valueChanged.connect(lambda v: self._gset(("interval_ms",), v))
        f1.addRow("Intervalo de atualização", self.sp_interval)
        self.sp_tw, self.sp_tb = _spin(30, 120, 1, " °C"), _spin(30, 130, 1, " °C")
        self.sp_pw, self.sp_pb = _spin(10, 100, 1, " %"), _spin(10, 100, 1, " %")
        self.sp_tw.valueChanged.connect(lambda v: self._gset(("thresholds", "temp", 0), v))
        self.sp_tb.valueChanged.connect(lambda v: self._gset(("thresholds", "temp", 1), v))
        self.sp_pw.valueChanged.connect(lambda v: self._gset(("thresholds", "pct", 0), v))
        self.sp_pb.valueChanged.connect(lambda v: self._gset(("thresholds", "pct", 1), v))
        f1.addRow("Temperatura: amarelo a partir de", self.sp_tw)
        f1.addRow("Temperatura: vermelho a partir de", self.sp_tb)
        f1.addRow("Porcentagem: amarelo a partir de", self.sp_pw)
        f1.addRow("Porcentagem: vermelho a partir de", self.sp_pb)
        lay.addWidget(g1)

        g2 = QGroupBox("Alertas (notificação na bandeja)")
        f2 = QFormLayout(g2)
        self.cb_alerts = QCheckBox("Avisar quando passar do limite")
        self.cb_alerts.toggled.connect(lambda c: self._gset(("alerts", "enabled"), c))
        self.sp_at = _spin(50, 120, 1, " °C")
        self.sp_at.valueChanged.connect(lambda v: self._gset(("alerts", "temp_c"), v))
        self.sp_ar = _spin(50, 100, 1, " %")
        self.sp_ar.valueChanged.connect(lambda v: self._gset(("alerts", "ram_pct"), v))
        f2.addRow("", self.cb_alerts)
        f2.addRow("Temperatura (CPU/GPU)", self.sp_at)
        f2.addRow("RAM em uso", self.sp_ar)
        lay.addWidget(g2)

        g3 = QGroupBox("Histórico em arquivo")
        f3 = QFormLayout(g3)
        self.cb_log = QCheckBox("Gravar histórico das leituras")
        self.cb_log.toggled.connect(self._log_toggled)
        self.sp_li = _spin(1, 600, 1, " s")
        self.sp_li.valueChanged.connect(lambda v: self._gset(("logging", "interval_s"), v))
        self.sp_lr = _spin(1, 365, 1, " dias")
        self.sp_lr.valueChanged.connect(lambda v: self._gset(("logging", "retention_days"), v))
        btn = QPushButton("Abrir pasta do histórico")
        btn.clicked.connect(self._open_folder)
        f3.addRow("", self.cb_log)
        f3.addRow("Gravar a cada", self.sp_li)
        f3.addRow("Apagar sessões com mais de", self.sp_lr)
        f3.addRow("", btn)
        lay.addWidget(g3)

        g4 = QGroupBox("Sistema")
        v4 = QVBoxLayout(g4)
        self.cb_auto = QCheckBox("Iniciar com o Windows")
        self.cb_auto.toggled.connect(self._autostart_toggled)
        v4.addWidget(self.cb_auto)
        self.lb_status = QLabel()
        self.lb_status.setWordWrap(True)
        v4.addWidget(self.lb_status)
        lay.addWidget(g4)
        lay.addStretch(1)
        return w

    def _gset(self, path, value):
        if self._loading:
            return
        node = self.cfg
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        self.changed.emit()

    def _log_toggled(self, c):
        if self._loading:
            return
        self.cfg["logging"]["enabled"] = c
        self.changed.emit()
        self.logging_changed.emit()

    def _autostart_toggled(self, c):
        if self._loading:
            return
        ok, msg = autostart.set_enabled(c, is_admin())
        self.lb_status.setText(msg)
        self._loading = True
        if not ok:
            self.cb_auto.setChecked(not c)
        else:
            self.cfg["start_with_windows"] = c
        self._loading = False
        self.changed.emit()

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.history_folder))

    def _load_general(self):
        c = self.cfg
        self._loading = True
        self.sp_interval.setValue(int(c["interval_ms"]))
        self.sp_tw.setValue(int(c["thresholds"]["temp"][0]))
        self.sp_tb.setValue(int(c["thresholds"]["temp"][1]))
        self.sp_pw.setValue(int(c["thresholds"]["pct"][0]))
        self.sp_pb.setValue(int(c["thresholds"]["pct"][1]))
        self.cb_alerts.setChecked(bool(c["alerts"]["enabled"]))
        self.sp_at.setValue(int(c["alerts"]["temp_c"]))
        self.sp_ar.setValue(int(c["alerts"]["ram_pct"]))
        self.cb_log.setChecked(bool(c["logging"]["enabled"]))
        self.sp_li.setValue(int(c["logging"]["interval_s"]))
        self.sp_lr.setValue(int(c["logging"]["retention_days"]))
        self.cb_auto.setChecked(bool(c.get("start_with_windows")))
        self._loading = False
        self.update_status()

    def update_status(self):
        lines = ["Administrador: " + ("sim" if is_admin() else "NÃO — as temperaturas da CPU podem não aparecer")]
        lines.append("Sensores da placa/CPU (LibreHardwareMonitor): " + (self.status.get("lhm_error") or "ok"))
        lines.append("GPU NVIDIA (NVML): " + (self.status.get("nv_error") or "ok"))
        self.lb_status.setText("\n".join(lines))

    # ============================================================== aba Histórico
    def _build_history_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        b1 = QPushButton("Atualizar lista")
        b1.clicked.connect(self._load_history)
        b2 = QPushButton("Abrir pasta")
        b2.clicked.connect(self._open_folder)
        top.addWidget(b1)
        top.addWidget(b2)
        top.addStretch(1)
        lay.addLayout(top)
        self.tbl_hist = QTableWidget(0, 4)
        self.tbl_hist.setHorizontalHeaderLabels(["Início", "Último registro", "Estado", "Arquivo"])
        self.tbl_hist.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl_hist.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_hist.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_hist.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_hist.verticalHeader().setVisible(False)
        self.tbl_hist.itemSelectionChanged.connect(self._hist_selected)
        lay.addWidget(self.tbl_hist, 2)
        self.txt_hist = QPlainTextEdit()
        self.txt_hist.setReadOnly(True)
        self.txt_hist.setPlaceholderText("Selecione uma sessão para ver o resumo: valores máximos e os últimos "
                                         "valores registrados antes de a sessão terminar.")
        lay.addWidget(self.txt_hist, 3)
        return w

    def _load_history(self):
        paths = history.list_sessions(self.history_folder)[:60]
        self.tbl_hist.setRowCount(len(paths))
        for r, p in enumerate(paths):
            info = history.quick_info(p)
            start = info["start"].strftime("%d/%m/%Y %H:%M:%S") if info["start"] else "?"
            state = "Encerrada normalmente" if info["clean"] else (
                "Em andamento ou terminou de forma inesperada")
            cells = [start, info["last"] or "—", state, os.path.basename(p)]
            for c, t in enumerate(cells):
                it = QTableWidgetItem(t)
                it.setData(Qt.UserRole, p)
                if not info["clean"]:
                    it.setForeground(QColor("#f87171"))
                self.tbl_hist.setItem(r, c, it)
        self.txt_hist.clear()

    def _hist_selected(self):
        rows = self.tbl_hist.selectedItems()
        if not rows:
            return
        path = rows[0].data(Qt.UserRole)
        try:
            s = history.summarize(path)
        except OSError as e:
            self.txt_hist.setPlainText(f"Não foi possível ler o arquivo: {e}")
            return
        out = [f"Arquivo: {os.path.basename(path)}",
               f"Registros: {s['rows']}   |   {s['first']}  →  {s['last_ts']}",
               "Encerrada normalmente." if s["clean"] else
               "NÃO foi encerrada normalmente (queda, reinício ou programa fechado à força).",
               ""]
        if s["last"]:
            out.append("Últimos valores registrados:")
            for k, (label, val) in s["last"].items():
                if k in history.KEY_COLUMNS:
                    out.append(f"  {label}: {val}")
            out.append("")
        temps = sorted(((v[1], v[0]) for k, v in s["max"].items() if "[°C]" in v[0]), reverse=True)[:8]
        if temps:
            out.append("Temperaturas máximas na sessão:")
            out += [f"  {label.replace(' [°C]', '')}: {val:.0f} °C" for val, label in temps]
            out.append("")
        for k in ("cpu.load", "ram.pct", "nv0.load", "nv0.vram_pct", "swap.pct"):
            if k in s["max"]:
                out.append(f"Máximo de {s['max'][k][0].split(' [')[0]}: {s['max'][k][1]:.0f}%")
        self.txt_hist.setPlainText("\n".join(out))

    # ============================================================== geral
    def _on_tab(self, i):
        if i == 3:
            self._load_history()
        elif i == 2:
            self.update_status()

    def rebuild_all(self):
        self._rebuild_tree()
        self._rebuild_live()
        self.cmb_screen.clear()
        for i, s in enumerate(QGuiApplication.screens()):
            g = s.geometry()
            self.cmb_screen.addItem(f"Tela {i + 1} ({g.width()}×{g.height()})")
        self._fill_list(min(max(0, self.list_widgets.currentRow()), len(self.cfg["widgets"]) - 1))
        self._load_general()
        if self.tabs.currentIndex() == 3:
            self._load_history()

    def set_catalog_ready(self, status):
        self.status = status
        self.rebuild_all()

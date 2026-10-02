"""Subaba 'Comandos sugeridos': mostra comandos do PowerShell ligados ao problema. Nada roda sozinho."""
import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

import cmd_suggestions
import diagnostics
from sensors import is_admin


class CommandsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.scenario, self.report, self.items, self.proc = "geral", None, [], None
        lay = QVBoxLayout(self)
        warn = QLabel("<b>Atenção:</b> " + cmd_suggestions.AVISO + " Comandos marcados com ⚠ <b>alteram</b> o sistema; "
                      "leia a descrição antes. O texto do comando pode ser editado antes de copiar ou executar.")
        warn.setWordWrap(True)
        warn.setStyleSheet("background:#3a3320;border:1px solid #8a7a2a;border-radius:6px;padding:8px;")
        lay.addWidget(warn)
        row = QHBoxLayout()
        self.cb_all = QCheckBox("Mostrar todos os comandos")
        self.cb_all.toggled.connect(self.refresh)
        row.addWidget(self.cb_all)
        self.lb_ctx = QLabel()
        row.addWidget(self.lb_ctx, 1)
        lay.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._show)
        split.addWidget(self.list)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.lb_title = QLabel()
        self.lb_title.setWordWrap(True)
        self.lb_title.setTextFormat(Qt.RichText)
        rl.addWidget(self.lb_title)
        self.ed = QPlainTextEdit()
        self.ed.setFont(QFont("Consolas", 10))
        self.ed.setMaximumHeight(110)
        rl.addWidget(self.ed)
        b = QHBoxLayout()
        self.btn_copy = QPushButton("Copiar comando")
        self.btn_copy.clicked.connect(self.copy)
        self.btn_run = QPushButton("Executar aqui…")
        self.btn_run.clicked.connect(self.run)
        self.btn_stop = QPushButton("Cancelar")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(lambda: self.proc and self.proc.kill())
        for x in (self.btn_copy, self.btn_run, self.btn_stop):
            b.addWidget(x)
        b.addStretch(1)
        rl.addLayout(b)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(QFont("Consolas", 9))
        self.out.setPlaceholderText("A saída aparece aqui, só depois que você escolher executar.")
        rl.addWidget(self.out, 1)
        split.addWidget(right)
        split.setSizes([360, 560])
        lay.addWidget(split, 1)
        self.refresh()

    # ------------------------------------------------------------------ lista
    def set_context(self, scenario, report=None):
        self.scenario, self.report = scenario, report
        self.refresh()

    def refresh(self):
        _label, tags = diagnostics.SCENARIOS.get(self.scenario, diagnostics.SCENARIOS["geral"])
        hyp = set()
        if self.report:
            for h in self.report.hypotheses[:2]:
                if h[2] > 0:
                    hyp |= diagnostics.HYP_TAGS.get(h[0], set())
        self.items = cmd_suggestions.suggest(tags, hyp or None, self.cb_all.isChecked())
        self.lb_ctx.setText(f"Foco: <b>{_label}</b>" + (" — ordenados pelas causas prováveis do último diagnóstico"
                                                         if hyp else ""))
        self.list.clear()
        last = None
        for i, (c, grp) in enumerate(self.items):
            if grp != last:
                last = grp
                h = QListWidgetItem(f"──  {grp}  ──")
                h.setFlags(Qt.NoItemFlags)
                h.setForeground(QColor("#7f8799"))
                h.setData(Qt.UserRole, -1)
                self.list.addItem(h)
            icon = "🔍" if c.kind == "leitura" else "⚠"
            it = QListWidgetItem(f"{icon}  {c.title}")
            it.setForeground(QColor("#e8eaf0" if c.kind == "leitura" else "#facc15"))
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        for r in range(self.list.count()):
            if self.list.item(r).data(Qt.UserRole) >= 0:
                self.list.setCurrentRow(r)
                break

    def _cur(self):
        it = self.list.currentItem()
        if it is None or it.data(Qt.UserRole) < 0:
            return None
        return self.items[it.data(Qt.UserRole)][0]

    def _show(self, _row):
        c = self._cur()
        if c is None:
            return
        if c.kind == "leitura":
            badge = "<span style='color:#4ade80'>🔍 Só consulta, não altera nada.</span>"
        else:
            badge = "<span style='color:#facc15'>⚠ Altera o sistema ou inicia reparo/verificação.</span>"
        if c.admin:
            badge += " <span style='color:#9aa3b5'>Exige administrador.</span>"
        self.lb_title.setText(f"<h3>{c.title}</h3><p>{c.desc}</p><p>{badge}</p>")
        self.ed.setPlainText(c.cmd)

    # ------------------------------------------------------------------ ações
    def copy(self):
        QGuiApplication.clipboard().setText(self.ed.toPlainText())
        self.out.appendPlainText("— comando copiado. Cole no PowerShell (para os que exigem administrador, abra como administrador).")

    def run(self):
        c = self._cur()
        text = self.ed.toPlainText().strip()
        if c is None or not text or self.proc:
            return
        if sys.platform != "win32":
            QMessageBox.information(self, "PC Monitor", "Executar comandos só funciona no Windows.")
            return
        if c.admin and not is_admin():
            QMessageBox.warning(self, "Administrador necessário",
                                "Este comando exige administrador. Feche o programa e abra pelo executar.bat, "
                                "ou copie o comando e rode num PowerShell aberto como administrador.")
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if c.kind != "leitura" else QMessageBox.Question)
        box.setWindowTitle("Executar comando?")
        box.setText(f"<b>{c.title}</b><br>Este comando será executado agora no PowerShell, só porque você escolheu.")
        box.setInformativeText(("Ele ALTERA o sistema ou inicia um reparo. Leia a descrição e confirme apenas se entendeu."
                                if c.kind != "leitura" else "É um comando de consulta: apenas lê informações.")
                               + "\n\n" + text)
        run_b = box.addButton("Executar", QMessageBox.AcceptRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box.exec()
        if box.clickedButton() is not run_b:
            return
        self.out.appendPlainText(f"\nPS> {text}\n")
        prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyRead.connect(self._read)
        self.proc.finished.connect(self._done)
        self.proc.start("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                           prefix + "& { " + text + " } | Out-String -Width 200"])
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def _read(self):
        data = bytes(self.proc.readAll()).replace(b"\x00", b"")
        self.out.insertPlainText(data.decode("utf-8", errors="replace").replace("\r\n", "\n"))
        self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())

    def _done(self, code, _st):
        self.out.appendPlainText(f"\n— terminou (código {code}).")
        self.proc = None
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

"""Aba Diagnóstico: análise completa com hipóteses, ferramentas de reparo e atalhos do Windows."""
import datetime as dt
import json
import os
import subprocess
import sys

from PySide6.QtCore import QProcess, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

import config
import diagnostics
import history
from sensors import is_admin

SEV_COLOR = {"crit": "#f87171", "warn": "#facc15", "info": "#9aa3b5", "ok": "#4ade80"}
SEV_ICON = {"crit": "✖", "warn": "▲", "info": "ℹ", "ok": "✔"}


class DiagThread(QThread):
    done = Signal(object, object)
    failed = Signal(str)

    def __init__(self, milestone, exclude_session, scenario="geral"):
        super().__init__()
        self.milestone, self.exclude, self.scenario = milestone, exclude_session, scenario

    def run(self):
        try:
            raw = diagnostics.collect()
            hist = history.unclean_last_values(config.history_dir(), exclude=self.exclude)
            recent = history.recent_stats(config.history_dir(), 24)
            self.done.emit(diagnostics.analyze(raw, hist, self.milestone, scenario=self.scenario, recent=recent), raw)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class DiagnosticsTab(QWidget):
    def __init__(self, cfg, get_session_path, on_change):
        super().__init__()
        self.cfg, self.get_session_path, self.on_change = cfg, get_session_path, on_change
        self.report, self.raw, self.thread, self.proc = None, None, None, None
        lay = QVBoxLayout(self)
        self.inner = QTabWidget()
        lay.addWidget(self.inner)
        self.inner.addTab(self._build_analysis(), "Análise")
        self.inner.addTab(self._build_tools(), "Ferramentas")
        self._show_milestone()
        self._intro()

    # ================================================================ análise
    def _build_analysis(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row0 = QHBoxLayout()
        row0.addWidget(QLabel("O que está acontecendo?"))
        self.cb_scn = QComboBox()
        for key, (label, _tags) in diagnostics.SCENARIOS.items():
            self.cb_scn.addItem(label, key)
        i = self.cb_scn.findData(self.cfg.get("diag_scenario", "geral"))
        self.cb_scn.setCurrentIndex(max(0, i))
        self.cb_scn.currentIndexChanged.connect(self._scn_changed)
        row0.addWidget(self.cb_scn, 1)
        lay.addLayout(row0)
        row = QHBoxLayout()
        self.btn_run = QPushButton("Executar diagnóstico")
        self.btn_run.clicked.connect(self.run_diag)
        self.btn_save = QPushButton("Salvar relatório…")
        self.btn_copy = QPushButton("Copiar relatório")
        self.btn_raw = QPushButton("Salvar dados brutos…")
        for b in (self.btn_save, self.btn_copy, self.btn_raw):
            b.setEnabled(False)
        self.btn_save.clicked.connect(self.save_report)
        self.btn_copy.clicked.connect(self.copy_report)
        self.btn_raw.clicked.connect(self.save_raw)
        for b in (self.btn_run, self.btn_save, self.btn_copy, self.btn_raw):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.lb_status = QLabel()
        self.lb_status.setWordWrap(True)
        lay.addWidget(self.lb_status)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._show_item)
        split.addWidget(self.list)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        split.addWidget(self.detail)
        split.setSizes([470, 450])
        lay.addWidget(split, 1)

        g = QGroupBox("Marco de comparação")
        gl = QHBoxLayout(g)
        self.lb_mile = QLabel()
        self.lb_mile.setWordWrap(True)
        b1 = QPushButton("Marcar mudança agora…")
        b1.setToolTip("Registra o momento de uma mudança (ex.: EXPO desativado, BIOS atualizada) para o relatório "
                      "comparar as quedas antes e depois.")
        b1.clicked.connect(self.set_milestone)
        b2 = QPushButton("Limpar")
        b2.clicked.connect(self.clear_milestone)
        gl.addWidget(self.lb_mile, 1)
        gl.addWidget(b1)
        gl.addWidget(b2)
        lay.addWidget(g)
        return w

    def _intro(self):
        self.list.clear()
        msg = ("<h3>Diagnóstico do PC</h3>"
               "<p>Escolha acima o que está acontecendo (lentidão, programas fechando, tela azul, calor, disco, internet, "
               "vídeo, Windows Update…) ou deixe em <b>Check-up geral</b>. O diagnóstico lê eventos do Windows, BIOS, memória, "
               "discos, drivers, rede, segurança e o histórico do monitor, e aponta a causa mais provável com o que fazer.</p>"
               "<p>Leva de 30 a 90 segundos. Nada é alterado no sistema.</p>")
        if sys.platform != "win32":
            msg += "<p><b>O diagnóstico completo só funciona no Windows.</b></p>"
        elif not is_admin():
            msg += "<p><b>Sem administrador:</b> alguns dados (SMART dos discos) podem faltar. Abra com o executar.bat.</p>"
        self.detail.setHtml(msg)

    def _scn_changed(self):
        self.cfg["diag_scenario"] = self.cb_scn.currentData()
        self.on_change()

    def run_diag(self):
        if self.thread and self.thread.isRunning():
            return
        self.btn_run.setEnabled(False)
        self.progress.show()
        self.lb_status.setText("Coletando dados do Windows… isso pode levar até 1 ou 2 minutos.")
        self.thread = DiagThread(self.cfg.get("milestone"), self.get_session_path(), self.cb_scn.currentData())
        self.thread.done.connect(self._finished)
        self.thread.failed.connect(self._failed)
        self.thread.start()

    def _failed(self, msg):
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.lb_status.setText("")
        QMessageBox.warning(self, "Diagnóstico", f"Não foi possível concluir o diagnóstico:\n\n{msg}")

    def _finished(self, report, raw):
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.report, self.raw = report, raw
        for b in (self.btn_save, self.btn_copy, self.btn_raw):
            b.setEnabled(True)
        crit = sum(1 for f in report.relevant if f.sev == "crit")
        warn = sum(1 for f in report.relevant if f.sev == "warn")
        self.lb_status.setText(f"Concluído em {report.created} — foco: {report.scenario_label()}. "
                               f"{crit} crítico(s), {warn} atenção. "
                               + (f"Avisos da coleta: {len(report.collection_errors)}." if report.collection_errors else ""))
        self.list.clear()
        top = report.top()
        if top:
            it = QListWidgetItem(f"★ Causa mais provável ({report.confidence}): {top[1]}")
            it.setForeground(QColor("#4cc9f0"))
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            it.setData(Qt.UserRole, -1)
            self.list.addItem(it)
        elif not any(f.sev in ("crit", "warn") for f in report.relevant):
            it = QListWidgetItem("✔  Nenhum problema encontrado para este foco")
            it.setForeground(QColor(SEV_COLOR["ok"]))
            it.setData(Qt.UserRole, -2)
            self.list.addItem(it)
        index = {id(f): i for i, f in enumerate(report.findings)}

        def add(f):
            it = QListWidgetItem(f"{SEV_ICON[f.sev]}  {f.title}")
            it.setForeground(QColor(SEV_COLOR[f.sev]))
            it.setData(Qt.UserRole, index[id(f)])
            self.list.addItem(it)

        for f in report.relevant:
            add(f)
        if report.others:
            sep = QListWidgetItem(f"──  Outros achados, fora do foco ({len(report.others)})  ──")
            sep.setFlags(Qt.NoItemFlags)
            sep.setForeground(QColor("#7f8799"))
            sep.setData(Qt.UserRole, -3)
            self.list.addItem(sep)
            for f in report.others:
                add(f)
        self.list.setCurrentRow(0)

    def _show_item(self, row):
        it = self.list.item(row)
        if it is None or not self.report:
            return
        idx = it.data(Qt.UserRole)
        if idx == -1:
            self.detail.setHtml(self._hyp_html())
            return
        if idx == -2:
            self.detail.setHtml("<h3 style='color:#4ade80'>Nada de errado encontrado</h3>"
                                "<p>Nenhum sinal de problema para o foco escolhido. Se o problema acontece só às vezes, "
                                "deixe o monitor rodando e execute o diagnóstico logo depois que ele ocorrer, ou escolha "
                                "<b>Check-up geral</b> para olhar tudo.</p>")
            return
        if idx < 0:
            return
        f = self.report.findings[idx]
        html = (f"<h3 style='color:{SEV_COLOR[f.sev]}'>{_esc(f.title)}</h3>"
                f"<p><small>{diagnostics.SEV_LABEL[f.sev]}</small></p>")
        if f.detail:
            html += f"<pre style='white-space:pre-wrap'>{_esc(f.detail)}</pre>"
        if f.advice:
            html += f"<p><b>O que fazer:</b> {_esc(f.advice)}</p>"
        self.detail.setHtml(html)

    def _hyp_html(self):
        r = self.report
        t = r.top()
        html = (f"<h3>★ {_esc(t[1])}</h3><p><b>Confiança:</b> {r.confidence} (pontos: {t[2]})</p>"
                "<p><b>Evidências encontradas:</b></p><ul>" + "".join(f"<li>{_esc(e)}</li>" for e in t[3]) + "</ul>"
                "<p><b>O que fazer, nesta ordem:</b></p><ol>" + "".join(f"<li>{_esc(a)}</li>" for a in t[4]) + "</ol>")
        others = [h for h in r.hypotheses[1:] if h[2] > 0]
        if others:
            html += "<p><b>Outras hipóteses (menos prováveis):</b></p><ul>" + "".join(
                f"<li>{_esc(h[1])} — pontos: {h[2]}<br><small>{_esc('; '.join(h[3]))}</small></li>" for h in others) + "</ul>"
        html += ("<p><small>É uma estimativa baseada em padrões do registro do Windows, não uma certeza. "
                 "Confirme com os testes indicados.</small></p>")
        return html

    # ---- exportar
    def _default_name(self, ext):
        return os.path.join(os.path.expanduser("~"), "Documents",
                            f"diagnostico-pc-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.{ext}")

    def save_report(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salvar relatório", self._default_name("txt"), "Texto (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.report.to_text())

    def copy_report(self):
        if self.report:
            QGuiApplication.clipboard().setText(self.report.to_text())
            self.lb_status.setText("Relatório copiado. Cole onde quiser (por exemplo, numa conversa com o Claude).")

    def save_raw(self):
        if not self.raw:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salvar dados brutos", self._default_name("json"), "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.raw, f, indent=1, ensure_ascii=False)

    # ---- marco
    def _show_milestone(self):
        m = self.cfg.get("milestone")
        if m and m.get("ts"):
            t = diagnostics.parse_ts(m["ts"])
            self.lb_mile.setText(f"Marco atual: <b>{_esc(m.get('note') or 'mudança')}</b> em "
                                 f"{t.strftime('%d/%m/%Y %H:%M') if t else m['ts']}. O relatório compara as quedas antes e depois.")
        else:
            self.lb_mile.setText("Nenhum marco definido. Use depois de uma mudança (EXPO desativado, BIOS nova, driver…).")

    def set_milestone(self):
        note, ok = QInputDialog.getText(self, "Marcar mudança", "O que você mudou?", text="EXPO/DOCP desativado")
        if ok:
            self.cfg["milestone"] = {"ts": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "note": note.strip()}
            self._show_milestone()
            self.on_change()

    def clear_milestone(self):
        self.cfg["milestone"] = None
        self._show_milestone()
        self.on_change()

    # ================================================================ ferramentas
    def _build_tools(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        g1 = QGroupBox("Verificação e reparo do Windows (exige administrador)")
        v1 = QVBoxLayout(g1)
        row = QHBoxLayout()
        self.tool_btns = []
        for text, prog, args, warn in (
            ("SFC /scannow", "sfc", ["/scannow"], True),
            ("DISM RestoreHealth", "DISM", ["/Online", "/Cleanup-Image", "/RestoreHealth"], True),
            ("CHKDSK C: (somente leitura)", "chkdsk", ["C:", "/scan"], False),
        ):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, p=prog, a=args, wn=warn, t=text: self.run_tool(t, p, a, wn))
            row.addWidget(b)
            self.tool_btns.append(b)
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_tool)
        row.addWidget(self.btn_cancel)
        v1.addLayout(row)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(QFont("Consolas", 9))
        self.out.setPlaceholderText("A saída do comando aparece aqui. SFC e DISM podem levar de 5 a 30 minutos.")
        v1.addWidget(self.out, 1)
        lay.addWidget(g1, 1)

        g2 = QGroupBox("Atalhos do Windows")
        gl = QGridLayout(g2)
        shortcuts = [
            ("Monitor de Confiabilidade", ["perfmon", "/rel"]),
            ("Visualizador de Eventos", "eventvwr.msc"),
            ("Gerenciador de Dispositivos", "devmgmt.msc"),
            ("Teste de Memória do Windows", "mdsched.exe"),
            ("Informações do Sistema", "msinfo32"),
            ("Gerenciamento de Disco", "diskmgmt.msc"),
            ("Windows Update", "ms-settings:windowsupdate"),
            ("Pasta de minidumps", "C:\\Windows\\Minidump"),
        ]
        for i, (text, cmd) in enumerate(shortcuts):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, c=cmd: self._launch(c))
            gl.addWidget(b, i // 4, i % 4)
        lay.addWidget(g2)
        return w

    def _launch(self, cmd):
        if sys.platform != "win32":
            QMessageBox.information(self, "PC Monitor", "Disponível apenas no Windows.")
            return
        try:
            if isinstance(cmd, list):
                subprocess.Popen(cmd)
            else:
                os.startfile(cmd)  # type: ignore[attr-defined]
        except OSError as e:
            QMessageBox.warning(self, "PC Monitor", f"Não foi possível abrir: {e}")

    def run_tool(self, title, prog, args, repairs):
        if self.proc is not None:
            return
        if sys.platform != "win32":
            QMessageBox.information(self, "PC Monitor", "Disponível apenas no Windows.")
            return
        if not is_admin():
            QMessageBox.warning(self, "PC Monitor", "Abra o programa como administrador (executar.bat) para usar esta ferramenta.")
            return
        msg = (f"Executar {title}?\n\n"
               + ("Este comando verifica e REPARA arquivos do Windows e pode levar de 5 a 30 minutos. "
                  "Não desligue o PC durante a execução." if repairs else
                  "Este comando só lê o disco e informa problemas, sem alterar nada."))
        if QMessageBox.question(self, "Confirmar", msg) != QMessageBox.Yes:
            return
        self.out.clear()
        self.out.appendPlainText(f"> {prog} {' '.join(args)}\n")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyRead.connect(self._read_tool)
        self.proc.finished.connect(self._tool_done)
        self.proc.start(prog, args)
        for b in self.tool_btns:
            b.setEnabled(False)
        self.btn_cancel.setEnabled(True)

    def _read_tool(self):
        data = bytes(self.proc.readAll()).replace(b"\x00", b"")
        try:
            import ctypes
            enc = f"cp{ctypes.windll.kernel32.GetOEMCP()}"  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            enc = "utf-8"
        text = data.decode(enc, errors="replace").replace("\r\n", "\n")
        for chunk in text.replace("\r", "\n").split("\n"):
            if chunk.strip():
                self.out.appendPlainText(chunk)

    def _tool_done(self, code, _status):
        self.out.appendPlainText(f"\n— terminou (código {code}).")
        self.proc = None
        for b in self.tool_btns:
            b.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def cancel_tool(self):
        if self.proc:
            self.proc.kill()

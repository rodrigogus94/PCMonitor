"""Histórico das leituras em CSV (uma sessão por arquivo). Cada linha é gravada e sincronizada em disco,
para sobreviver a uma tela azul. Sessões que não terminam com a marca '# fim-normal' são tratadas como quedas."""
import csv
import datetime as dt
import json
import os

TS_FMT = "%Y-%m-%d %H:%M:%S"
END_MARK = "# fim-normal"


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


class HistoryLogger:
    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.path = None
        self._f = None
        self._w = None
        self._ids = []

    @property
    def active(self):
        return self._f is not None

    def start(self, catalog):
        self.close()
        self._ids = list(catalog.keys())
        name = dt.datetime.now().strftime("sessao-%Y%m%d-%H%M%S.csv")
        self.path = os.path.join(self.folder, name)
        self._f = open(self.path, "w", newline="", encoding="utf-8")
        legend = {i: f"{m.label} [{m.unit or 'texto'}]" for i, m in catalog.items()}
        self._f.write(f"# inicio={dt.datetime.now().strftime(TS_FMT)}\n")
        self._f.write("# legenda=" + json.dumps(legend, ensure_ascii=False) + "\n")
        self._w = csv.writer(self._f)
        self._w.writerow(["timestamp"] + self._ids)
        self._sync()

    def _sync(self):
        self._f.flush()
        try:
            os.fsync(self._f.fileno())
        except OSError:
            pass

    def write(self, values):
        if not self._f:
            return
        row = [dt.datetime.now().strftime(TS_FMT)] + [_fmt(values.get(i)) for i in self._ids]
        self._w.writerow(row)
        self._sync()

    def close(self):
        if self._f:
            try:
                self._f.write(f"{END_MARK}={dt.datetime.now().strftime(TS_FMT)}\n")
                self._sync()
                self._f.close()
            except OSError:
                pass
        self._f = None
        self._w = None


# ---------------------------------------------------------------------- leitura
def list_sessions(folder):
    try:
        names = [n for n in os.listdir(folder) if n.startswith("sessao-") and n.endswith(".csv")]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names, reverse=True)]


def _tail_lines(path, size=8192):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        f.seek(max(0, end - size))
        data = f.read().decode("utf-8", errors="replace")
    return [ln for ln in data.splitlines() if ln.strip()]


def quick_info(path):
    """Informações baratas (só lê o fim do arquivo): início, último registro e se terminou normalmente."""
    base = os.path.basename(path)
    try:
        start = dt.datetime.strptime(base[7:22], "%Y%m%d-%H%M%S")
    except ValueError:
        start = None
    info = {"path": path, "start": start, "last": None, "clean": False, "end_normal": None}
    try:
        lines = _tail_lines(path)
    except OSError:
        return info
    if lines and lines[-1].startswith(END_MARK):
        info["clean"] = True
        info["end_normal"] = lines[-1].split("=", 1)[-1]
        lines = lines[:-1]
    for ln in reversed(lines):
        if not ln.startswith("#") and ln[:4].isdigit():
            info["last"] = ln.split(",", 1)[0]
            break
    return info


def summarize(path):
    """Leitura completa: máximos, últimos valores e linha de chegada."""
    legend, header, rows, clean = {}, None, [], False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if ln.startswith("# legenda="):
                try:
                    legend = json.loads(ln.split("=", 1)[1])
                except ValueError:
                    pass
            elif ln.startswith(END_MARK):
                clean = True
            elif ln.startswith("#") or not ln.strip():
                continue
            elif header is None:
                header = next(csv.reader([ln]))
            else:
                try:
                    r = next(csv.reader([ln]))
                except StopIteration:
                    continue
                if header and len(r) == len(header):
                    rows.append(r)
    out = {"path": path, "clean": clean, "rows": len(rows), "first": None, "last_ts": None,
           "max": {}, "last": {}, "legend": legend}
    if not header or not rows:
        return out
    out["first"], out["last_ts"] = rows[0][0], rows[-1][0]
    for idx, col in enumerate(header[1:], start=1):
        label = legend.get(col, col)
        peak = None
        for r in rows:
            try:
                x = float(r[idx])
            except ValueError:
                continue
            if peak is None or x > peak:
                peak = x
        if peak is not None:
            out["max"][col] = (label, peak)
        if rows[-1][idx] != "":
            out["last"][col] = (label, rows[-1][idx])
    return out


KEY_COLUMNS = ["cpu.temp", "cpu.load", "ram.pct", "nv0.temp", "nv0.load", "proc.top"]


def describe_last(summary):
    parts = []
    names = {"cpu.temp": "CPU {} °C", "cpu.load": "uso CPU {}%", "ram.pct": "RAM {}%",
             "nv0.temp": "GPU {} °C", "nv0.load": "uso GPU {}%", "proc.top": "maior RAM: {}"}
    for k in KEY_COLUMNS:
        if k in summary["last"]:
            val = summary["last"][k][1]
            try:
                val = f"{float(val):.0f}"
            except ValueError:
                pass
            parts.append(names[k].format(val))
    return ", ".join(parts)


def find_unclean(folder):
    """Última sessão que não terminou normalmente (ou None)."""
    for path in list_sessions(folder)[:1]:
        info = quick_info(path)
        if not info["clean"] and info["last"]:
            return info
    return None


def cleanup(folder, days):
    import time
    limit = time.time() - days * 86400
    for path in list_sessions(folder):
        try:
            if os.path.getmtime(path) < limit:
                os.remove(path)
        except OSError:
            pass

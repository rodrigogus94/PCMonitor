"""Coleta de sensores: psutil (CPU/RAM/disco/rede), NVML (NVIDIA) e LibreHardwareMonitor (temperaturas do Ryzen etc.)."""
import ctypes
import datetime as _dt
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import psutil

try:
    import pynvml  # pacote nvidia-ml-py
except Exception:  # noqa: BLE001
    pynvml = None

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")


@dataclass
class Metric:
    id: str
    label: str                  # nome completo (lista de configurações)
    short: str = ""             # nome curto (widget)
    unit: str = ""              # %, °C, GB, W, MHz, RPM, V, B/s, s ou "" (texto)
    group: str = "Sistema"
    vmax: Optional[float] = None  # valor máximo (para a barrinha)
    text: bool = False
    alert: bool = True          # colorir por limites (verde/amarelo/vermelho)

    def __post_init__(self):
        if not self.short:
            self.short = self.label


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def format_value(m, v):
    if v is None:
        return "—"
    if m is None:
        return str(v)
    if m.text:
        return str(v)
    u = m.unit
    try:
        if u == "B/s":
            for lim, suf in ((1 << 30, "GB/s"), (1 << 20, "MB/s"), (1 << 10, "KB/s")):
                if v >= lim:
                    return f"{v / lim:.1f} {suf}"
            return f"{v:.0f} B/s"
        if u == "s":
            d, r = divmod(int(v), 86400)
            h, r = divmod(r, 3600)
            mi = r // 60
            return f"{d}d {h}h {mi:02d}m" if d else f"{h}h {mi:02d}m"
        if u == "%":
            return f"{v:.1f}%" if v < 10 else f"{v:.0f}%"
        if u == "°C":
            return f"{v:.0f} °C"
        if u == "GB":
            return f"{v:.1f} GB"
        if u == "W":
            return f"{v:.1f} W"
        if u == "MHz":
            return f"{v:.0f} MHz"
        if u == "RPM":
            return f"{v:.0f} RPM"
        if u == "V":
            return f"{v:.3f} V"
        return f"{v:.1f}"
    except (TypeError, ValueError):
        return str(v)


_LHM_UNITS = {
    "Temperature": "°C", "Load": "%", "Power": "W", "Clock": "MHz", "Fan": "RPM", "Voltage": "V",
}


def _lhm_group(hw_type):
    if hw_type == "Cpu":
        return "CPU (sensores)"
    if hw_type.startswith("Gpu"):
        return "GPU (sensores)"
    if hw_type in ("Motherboard", "SuperIO", "EmbeddedController"):
        return "Placa-mãe (sensores)"
    if hw_type == "Storage":
        return "Armazenamento (sensores)"
    return "Outros (sensores)"


class Collector:
    def __init__(self):
        self.catalog = {}
        self.lhm_error = None
        self.nv_error = None
        self._tick = 0
        self._last_t = time.monotonic()
        self._last_net = psutil.net_io_counters()
        self._last_disk = psutil.disk_io_counters()
        self._top_t = 0.0
        self._top_text = None
        self._nv = []
        self._lhm_comp = None
        self._lhm_hw = []         # (hardware, é_storage)
        self._lhm_sensors = []    # (id, sensor)
        self._alias = {}          # id_alias -> id_sensor
        self._clock_ids = []
        self._last_crash = None
        psutil.cpu_percent(None)
        self._build_base()
        self._init_nvml()
        self._init_lhm()
        if sys.platform == "win32":
            self._add(Metric("sys.since_crash", "Tempo sem queda (desde o último reinício inesperado)",
                             "Sem queda há", "s", "Sistema", alert=False))
            threading.Thread(target=self._fetch_last_crash, daemon=True).start()

    def _fetch_last_crash(self):
        """Lê do registro de eventos a data do último reinício inesperado (Kernel-Power 41)."""
        cmd = ("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41} "
               "-MaxEvents 1 -ErrorAction SilentlyContinue | ForEach-Object { $_.TimeCreated.ToString('s') }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                               capture_output=True, text=True, timeout=90,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            line = (r.stdout or "").strip().splitlines()
            if line:
                self._last_crash = _dt.datetime.fromisoformat(line[-1].strip())
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ catálogo
    def _add(self, m):
        self.catalog[m.id] = m

    def _build_base(self):
        total_gb = psutil.virtual_memory().total / 2 ** 30
        self._add(Metric("cpu.load", "CPU · uso total", "CPU", "%", "CPU", 100))
        self._add(Metric("ram.pct", "RAM · uso", "RAM", "%", "Memória", 100))
        self._add(Metric("ram.used", "RAM · em uso", "RAM em uso", "GB", "Memória", total_gb, alert=False))
        self._add(Metric("ram.free", "RAM · disponível", "RAM livre", "GB", "Memória", total_gb, alert=False))
        self._add(Metric("swap.pct", "Arquivo de paginação · uso", "Pagefile", "%", "Memória", 100))
        self._add(Metric("proc.top", "Processo que mais usa RAM", "Maior uso RAM", "", "Memória", text=True))
        self._add(Metric("net.down", "Rede · download", "↓ Rede", "B/s", "Rede", alert=False))
        self._add(Metric("net.up", "Rede · upload", "↑ Rede", "B/s", "Rede", alert=False))
        self._add(Metric("disk.read", "Disco · leitura (total)", "Disco leitura", "B/s", "Disco", alert=False))
        self._add(Metric("disk.write", "Disco · gravação (total)", "Disco gravação", "B/s", "Disco", alert=False))
        self._add(Metric("sys.uptime", "Tempo ligado", "Ligado há", "s", "Sistema", alert=False))
        self._parts = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in p.opts or not p.fstype:
                continue
            try:
                if psutil.disk_usage(p.mountpoint).total <= 0:
                    continue
            except Exception:  # noqa: BLE001
                continue
            mid = f"disk.usage:{p.mountpoint}"
            name = p.mountpoint.rstrip("\\/") or p.mountpoint
            self._add(Metric(mid, f"Disco {name} · espaço usado", f"Disco {name}", "%", "Disco", 100))
            self._parts.append((mid, p.mountpoint))

    # ------------------------------------------------------------------ NVIDIA
    def _init_nvml(self):
        if pynvml is None:
            self.nv_error = "pacote nvidia-ml-py não instalado"
            return
        try:
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode(errors="replace")
                short = name.replace("NVIDIA ", "").replace("GeForce ", "")
                vram_gb = pynvml.nvmlDeviceGetMemoryInfo(h).total / 2 ** 30
                p = f"nv{i}"
                g = f"GPU NVIDIA ({short})"
                self._add(Metric(f"{p}.load", f"{short} · uso", "GPU", "%", g, 100))
                self._add(Metric(f"{p}.temp", f"{short} · temperatura", "GPU temp", "°C", g, 100))
                self._add(Metric(f"{p}.vram_pct", f"{short} · VRAM (%)", "VRAM", "%", g, 100))
                self._add(Metric(f"{p}.vram_used", f"{short} · VRAM usada", "VRAM usada", "GB", g, vram_gb, alert=False))
                self._add(Metric(f"{p}.power", f"{short} · consumo", "GPU watts", "W", g, alert=False))
                self._add(Metric(f"{p}.clock", f"{short} · clock do núcleo", "GPU clock", "MHz", g, alert=False))
                self._add(Metric(f"{p}.fan", f"{short} · ventoinha", "GPU fan", "%", g, 100, alert=False))
                self._nv.append((p, h))
        except Exception as e:  # noqa: BLE001
            self.nv_error = str(e)
            self._nv = []

    @staticmethod
    def _try(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            return None

    def _sample_nv(self, v):
        for p, h in self._nv:
            u = self._try(pynvml.nvmlDeviceGetUtilizationRates, h)
            v[f"{p}.load"] = float(u.gpu) if u else None
            t = self._try(pynvml.nvmlDeviceGetTemperature, h, pynvml.NVML_TEMPERATURE_GPU)
            v[f"{p}.temp"] = float(t) if t is not None else None
            mem = self._try(pynvml.nvmlDeviceGetMemoryInfo, h)
            if mem:
                v[f"{p}.vram_pct"] = mem.used * 100.0 / mem.total if mem.total else None
                v[f"{p}.vram_used"] = mem.used / 2 ** 30
            pw = self._try(pynvml.nvmlDeviceGetPowerUsage, h)
            v[f"{p}.power"] = pw / 1000.0 if pw is not None else None
            ck = self._try(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_GRAPHICS)
            v[f"{p}.clock"] = float(ck) if ck is not None else None
            fn = self._try(pynvml.nvmlDeviceGetFanSpeed, h)
            v[f"{p}.fan"] = float(fn) if fn is not None else None

    # ------------------------------------------------------------------ LibreHardwareMonitor
    def _init_lhm(self):
        if sys.platform != "win32":
            self.lhm_error = "LibreHardwareMonitor só funciona no Windows"
            return
        try:
            import clr  # pythonnet
        except Exception as e:  # noqa: BLE001
            self.lhm_error = f"pythonnet não instalado ({e})"
            return
        main_dll = os.path.join(LIB_DIR, "LibreHardwareMonitorLib.dll")
        if not os.path.exists(main_dll):
            self.lhm_error = "LibreHardwareMonitorLib.dll não encontrada em libs/ (rode setup_lhm.py)"
            return
        try:
            for fn in sorted(os.listdir(LIB_DIR)):
                if fn.lower().endswith(".dll") and fn != "LibreHardwareMonitorLib.dll":
                    try:
                        clr.AddReference(os.path.join(LIB_DIR, fn))
                    except Exception:  # noqa: BLE001
                        pass
            clr.AddReference(main_dll)
            from LibreHardwareMonitor.Hardware import Computer  # type: ignore
            c = Computer()
            c.IsCpuEnabled = True
            c.IsGpuEnabled = True
            c.IsMotherboardEnabled = True
            c.IsStorageEnabled = True
            c.IsMemoryEnabled = False
            c.IsNetworkEnabled = False
            c.Open()
            self._lhm_comp = c
            self._collect_lhm_hardware()
        except Exception as e:  # noqa: BLE001
            self.lhm_error = f"falha ao iniciar LibreHardwareMonitor: {e}"
            self._lhm_comp = None
            return
        if not self._lhm_sensors:
            self.lhm_error = "nenhum sensor encontrado (execute como administrador)"

    def _collect_lhm_hardware(self):
        def walk(hw):
            yield hw
            for sub in hw.SubHardware:
                yield from walk(sub)

        for top in self._lhm_comp.Hardware:
            for hw in walk(top):
                hw_type = str(hw.HardwareType)
                if hw_type in ("Memory", "Network"):
                    continue
                self._lhm_hw.append((hw, hw_type == "Storage"))
                try:
                    hw.Update()
                except Exception:  # noqa: BLE001
                    pass
                for s in hw.Sensors:
                    st = str(s.SensorType)
                    unit = _LHM_UNITS.get(st)
                    if not unit:
                        continue
                    sid = "lhm:" + str(s.Identifier)
                    tag = {"Cpu": "CPU", "Storage": "SSD/HD"}.get(hw_type, "GPU" if hw_type.startswith("Gpu") else "")
                    short = f"{tag} {s.Name}".strip() if tag != "SSD/HD" else f"{str(hw.Name)[:14]} {s.Name}"
                    vmax = 100 if unit in ("°C", "%") else None
                    self._add(Metric(sid, f"{hw.Name} · {s.Name}", short, unit, _lhm_group(hw_type), vmax,
                                     alert=unit in ("°C", "%")))
                    self._lhm_sensors.append((sid, s))
        self._make_cpu_aliases()

    def _make_cpu_aliases(self):
        temps, powers, clocks = [], [], []
        for sid, s in self._lhm_sensors:
            m = self.catalog[sid]
            if m.group != "CPU (sensores)":
                continue
            if m.unit == "°C":
                temps.append((sid, str(s.Name)))
            elif m.unit == "W":
                powers.append((sid, str(s.Name)))
            elif m.unit == "MHz" and "Core" in str(s.Name) and "Effective" not in str(s.Name):
                clocks.append(sid)

        def pick(items, prefs):
            for pref in prefs:
                for sid, name in items:
                    if pref.lower() in name.lower():
                        return sid
            return items[0][0] if items else None

        t = pick(temps, ["Tctl", "Tdie", "Package", "CPU"])
        if t:
            self._alias["cpu.temp"] = t
            self._add(Metric("cpu.temp", "CPU · temperatura", "CPU temp", "°C", "CPU", 100))
        w = pick(powers, ["Package"])
        if w:
            self._alias["cpu.power"] = w
            self._add(Metric("cpu.power", "CPU · consumo", "CPU watts", "W", "CPU", alert=False))
        if clocks:
            self._clock_ids = clocks
            self._add(Metric("cpu.clock", "CPU · clock máximo entre núcleos", "CPU clock", "MHz", "CPU", alert=False))

    def _sample_lhm(self, v):
        if not self._lhm_comp:
            return
        for hw, is_storage in self._lhm_hw:
            if is_storage and self._tick % 5:
                continue
            try:
                hw.Update()
            except Exception:  # noqa: BLE001
                pass
        for sid, s in self._lhm_sensors:
            try:
                val = s.Value
                v[sid] = float(val) if val is not None else None
            except Exception:  # noqa: BLE001
                v[sid] = None
        for alias, src in self._alias.items():
            v[alias] = v.get(src)
        if self._clock_ids:
            vals = [v.get(i) for i in self._clock_ids if v.get(i) is not None]
            v["cpu.clock"] = max(vals) if vals else None

    # ------------------------------------------------------------------ amostra
    def _update_top_process(self):
        agg = {}
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                mi = p.info["memory_info"]
                if mi:
                    name = p.info["name"] or "?"
                    agg[name] = agg.get(name, 0) + mi.rss
            except Exception:  # noqa: BLE001
                pass
        if agg:
            name, tot = max(agg.items(), key=lambda kv: kv[1])
            self._top_text = f"{name} {tot / 2 ** 30:.1f} GB" if tot >= 2 ** 30 else f"{name} {tot / 2 ** 20:.0f} MB"

    def sample(self):
        now = time.monotonic()
        dt = max(now - self._last_t, 1e-3)
        self._last_t = now
        self._tick += 1
        v = {}
        v["cpu.load"] = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        v["ram.pct"] = vm.percent
        v["ram.used"] = vm.used / 2 ** 30
        v["ram.free"] = vm.available / 2 ** 30
        try:
            v["swap.pct"] = psutil.swap_memory().percent
        except Exception:  # noqa: BLE001
            v["swap.pct"] = None
        n = psutil.net_io_counters()
        if n and self._last_net:
            v["net.down"] = max(0.0, (n.bytes_recv - self._last_net.bytes_recv) / dt)
            v["net.up"] = max(0.0, (n.bytes_sent - self._last_net.bytes_sent) / dt)
        self._last_net = n
        d = psutil.disk_io_counters()
        if d and self._last_disk:
            v["disk.read"] = max(0.0, (d.read_bytes - self._last_disk.read_bytes) / dt)
            v["disk.write"] = max(0.0, (d.write_bytes - self._last_disk.write_bytes) / dt)
        self._last_disk = d
        v["sys.uptime"] = time.time() - psutil.boot_time()
        if "sys.since_crash" in self.catalog:
            v["sys.since_crash"] = ((_dt.datetime.now() - self._last_crash).total_seconds()
                                    if self._last_crash else None)
        for mid, mount in self._parts:
            try:
                v[mid] = psutil.disk_usage(mount).percent
            except Exception:  # noqa: BLE001
                v[mid] = None
        if now - self._top_t >= 3:
            self._top_t = now
            self._update_top_process()
        v["proc.top"] = self._top_text
        self._sample_nv(v)
        self._sample_lhm(v)
        return v

    def close(self):
        try:
            if self._lhm_comp:
                self._lhm_comp.Close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if pynvml and self._nv:
                pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    # Teste rápido no terminal:  python sensors.py
    print("Administrador:", is_admin())
    col = Collector()
    print("NVML:", col.nv_error or "ok")
    print("LibreHardwareMonitor:", col.lhm_error or "ok")
    time.sleep(1)
    vals = col.sample()
    for mid, m in sorted(col.catalog.items(), key=lambda kv: (kv[1].group, kv[1].label)):
        print(f"[{m.group}] {m.label}: {format_value(m, vals.get(mid))}")
    col.close()

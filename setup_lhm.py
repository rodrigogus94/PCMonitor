"""Baixa o LibreHardwareMonitor (biblioteca que lê temperaturas da CPU/placa/SSD) para a pasta libs/.
Uso:  python setup_lhm.py"""
import io
import json
import os
import sys
import urllib.request
import zipfile

API = "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"
LIBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")
MANUAL = ("\nSe o download automático falhar, faça manualmente:\n"
          "  1. Abra https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest\n"
          "  2. Baixe o arquivo .zip principal (LibreHardwareMonitor.zip)\n"
          f"  3. Extraia TODOS os arquivos .dll dele para: {LIBS}\n")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "pcmonitor-setup"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def pick_asset(assets):
    zips = [a for a in assets if a["name"].lower().endswith(".zip")]
    for a in zips:
        if a["name"].lower() == "librehardwaremonitor.zip":
            return a
    for a in zips:
        n = a["name"].lower()
        if "librehardwaremonitor" in n and ".net" not in n and "net8" not in n and "net9" not in n:
            return a
    return zips[0] if zips else None


def main():
    os.makedirs(LIBS, exist_ok=True)
    try:
        rel = json.loads(get(API))
        asset = pick_asset(rel.get("assets", []))
        if not asset:
            raise RuntimeError("nenhum .zip encontrado na release")
        print(f"Baixando {asset['name']} ({rel.get('tag_name')})…")
        data = get(asset["browser_download_url"])
        count = 0
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.filename.lower().endswith(".dll"):
                    with open(os.path.join(LIBS, os.path.basename(info.filename)), "wb") as f:
                        f.write(z.read(info))
                    count += 1
        if not os.path.exists(os.path.join(LIBS, "LibreHardwareMonitorLib.dll")):
            raise RuntimeError("LibreHardwareMonitorLib.dll não veio no zip")
        print(f"OK: {count} DLLs em {LIBS}")
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: {e}")
        print(MANUAL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Diagnóstico do Windows: coleta via PowerShell + análise com hipóteses e recomendações em português."""
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

LOOKBACK_DAYS = 30

PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$start = (Get-Date).AddDays(-30)
$start90 = (Get-Date).AddDays(-90)
$r = [ordered]@{}
$script:errs = @()
function D($x) { if ($x) { $x.ToString('s') } else { $null } }
function Sec($name, [scriptblock]$sb) {
  try { $r[$name] = & $sb } catch { $script:errs += ($name + ': ' + $_.Exception.Message) }
}
function SecL($name, [scriptblock]$sb) {
  try { $r[$name] = @(& $sb) } catch { $script:errs += ($name + ': ' + $_.Exception.Message) }
}
function Ev($filter, $n) {
  Get-WinEvent -FilterHashtable $filter -MaxEvents $n -ErrorAction SilentlyContinue | ForEach-Object {
    $m = ("" + $_.Message) -replace '\s+', ' '
    if ($m.Length -gt 400) { $m = $m.Substring(0, 400) }
    [pscustomobject]@{ t = (D $_.TimeCreated); id = $_.Id; p = $_.ProviderName; m = $m }
  }
}

Sec 'os' {
  $o = Get-CimInstance Win32_OperatingSystem
  $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
  $sh = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\WindowsSelfHost\Applicability'
  [pscustomobject]@{ caption = $o.Caption; build = "$($o.BuildNumber)"; version = $o.Version; display = $cv.DisplayVersion
    ubr = $cv.UBR; boot = (D $o.LastBootUpTime); branch = $sh.BranchName; ring = $sh.Ring; content = $sh.ContentType
    flight = "$($sh.IsBuildFlightingEnabled)"; ramKb = $o.TotalVisibleMemorySize; ramFreeKb = $o.FreePhysicalMemory }
}
Sec 'board' {
  $b = Get-CimInstance Win32_BaseBoard
  $bios = Get-CimInstance Win32_BIOS
  $cs = Get-CimInstance Win32_ComputerSystem
  $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
  [pscustomobject]@{ maker = $cs.Manufacturer; model = $cs.Model; board = $b.Product; biosVersion = $bios.SMBIOSBIOSVersion
    biosDate = (D $bios.ReleaseDate); cpu = ("" + $cpu.Name).Trim(); cores = $cpu.NumberOfCores; threads = $cpu.NumberOfLogicalProcessors }
}
SecL 'ram' {
  Get-CimInstance Win32_PhysicalMemory | ForEach-Object {
    [pscustomobject]@{ slot = $_.DeviceLocator; gb = [math]::Round($_.Capacity / 1GB, 1); rated = $_.Speed
      configured = $_.ConfiguredClockSpeed; maker = ("" + $_.Manufacturer).Trim(); part = ("" + $_.PartNumber).Trim()
      type = $_.SMBIOSMemoryType; volt = $_.ConfiguredVoltage }
  }
}
SecL 'disks' {
  Get-PhysicalDisk | ForEach-Object {
    $d = $_
    $c = $d | Get-StorageReliabilityCounter
    [pscustomobject]@{ name = $d.FriendlyName; media = "$($d.MediaType)"; bus = "$($d.BusType)"; health = "$($d.HealthStatus)"
      op = "$($d.OperationalStatus)"; gb = [math]::Round($d.Size / 1GB); temp = $c.Temperature; wear = $c.Wear
      readErr = $c.ReadErrorsTotal; writeErr = $c.WriteErrorsTotal; hours = $c.PowerOnHours }
  }
}
SecL 'volumes' {
  Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    [pscustomobject]@{ letter = "$($_.DriveLetter)"; label = $_.FileSystemLabel; fs = $_.FileSystem
      gb = [math]::Round($_.Size / 1GB, 1); freeGb = [math]::Round($_.SizeRemaining / 1GB, 1) }
  }
}
SecL 'gpus' {
  Get-CimInstance Win32_VideoController | ForEach-Object {
    [pscustomobject]@{ name = $_.Name; driver = $_.DriverVersion; date = (D $_.DriverDate) }
  }
}
SecL 'pnp' {
  Get-PnpDevice -PresentOnly | Where-Object { $_.Status -ne 'OK' } | ForEach-Object {
    [pscustomobject]@{ cls = $_.Class; name = $_.FriendlyName; status = "$($_.Status)"; problem = "$($_.Problem)" }
  }
}
Sec 'power' {
  $p = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
  $pend = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
  [pscustomobject]@{ fastStartup = $p.HiberbootEnabled; pendingReboot = [bool]$pend; plan = ((powercfg /getactivescheme | Out-String).Trim()) }
}
SecL 'hotfix' {
  Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 8 | ForEach-Object {
    [pscustomobject]@{ id = $_.HotFixID; on = (D $_.InstalledOn) }
  }
}
SecL 'minidumps' {
  Get-ChildItem 'C:\Windows\Minidump' | ForEach-Object { [pscustomobject]@{ name = $_.Name; t = (D $_.LastWriteTime); kb = [math]::Round($_.Length / 1KB) } }
}
SecL 'ev_power'  { Ev @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41; StartTime=$start} 500 }
SecL 'ev_bugcheck' { Ev @{LogName='System'; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; Id=1001; StartTime=$start} 500 }
SecL 'ev_whea' { Ev @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$start} 200 }
SecL 'ev_disk' { Ev @{LogName='System'; ProviderName='disk','Ntfs','stornvme','storahci','storport'; Level=1,2,3; StartTime=$start} 200 }
SecL 'ev_dump' { Ev @{LogName='System'; ProviderName='volmgr'; Id=161; StartTime=$start} 200 }
SecL 'ev_memdiag' { Ev @{LogName='System'; ProviderName='Microsoft-Windows-MemoryDiagnostics-Results','Microsoft-Windows-Memory-Diagnostic-Task-Handler'; StartTime=$start90} 50 }
SecL 'ev_gpu' { Ev @{LogName='System'; ProviderName='Display','nvlddmkm','amdkmdag','amdwddmg'; StartTime=$start} 100 }
SecL 'ev_exhaust' { Ev @{LogName='System'; ProviderName='Microsoft-Windows-Resource-Exhaustion-Detector'; Id=2004; StartTime=$start} 50 }
SecL 'ev_apps' {
  Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'; Id=1000; StartTime=$start} -MaxEvents 600 -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_.Properties
    [pscustomobject]@{ t = (D $_.TimeCreated); app = "$($p[0].Value)"; module = "$($p[3].Value)"; code = "$($p[6].Value)" }
  }
}
SecL 'ev_hangs' {
  Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Hang'; Id=1002; StartTime=$start} -MaxEvents 300 -ErrorAction SilentlyContinue | ForEach-Object {
    [pscustomobject]@{ t = (D $_.TimeCreated); app = "$($_.Properties[0].Value)" }
  }
}
SecL 'ev_update' { Ev @{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; Level=2; StartTime=$start} 50 }
SecL 'startup' {
  Get-CimInstance Win32_StartupCommand | ForEach-Object {
    $c = "" + $_.Command
    if ($c.Length -gt 120) { $c = $c.Substring(0, 120) }
    [pscustomobject]@{ name = $_.Name; loc = $_.Location; cmd = $c }
  }
}
Sec 'perf' {
  $before = @{}
  Get-Process | ForEach-Object { if ($_.CPU) { $before[$_.Id] = $_.CPU } }
  $cpu = @(); $dq = @(); $dl = @(); $db = @(); $cm = @()
  for ($i = 0; $i -lt 3; $i++) {
    Start-Sleep -Seconds 1
    $p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
    $d = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'"
    $m = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
    $cpu += [double]$p.PercentProcessorTime; $dq += [double]$d.AvgDiskQueueLength
    $dl += [double]$d.AvgDisksecPerTransfer; $db += [double]$d.PercentDiskTime; $cm += [double]$m.PercentCommittedBytesInUse
  }
  $n = [Environment]::ProcessorCount
  $procs = Get-Process | ForEach-Object {
    $c0 = $before[$_.Id]
    $pc = 0
    if ($c0 -ne $null -and $_.CPU) { $pc = [math]::Round((($_.CPU - $c0) / 3) / $n * 100, 1) }
    [pscustomobject]@{ name = $_.ProcessName; cpu = $pc; ramMb = [math]::Round($_.WorkingSet64 / 1MB) }
  }
  $by = $procs | Group-Object name | ForEach-Object {
    [pscustomobject]@{ name = $_.Name; cpu = [math]::Round(($_.Group | Measure-Object cpu -Sum).Sum, 1); ramMb = ($_.Group | Measure-Object ramMb -Sum).Sum; n = $_.Count }
  }
  [pscustomobject]@{
    cpuAvg = [math]::Round(($cpu | Measure-Object -Average).Average, 1)
    diskQueue = [math]::Round(($dq | Measure-Object -Average).Average, 2)
    diskLatMs = [math]::Round((($dl | Measure-Object -Average).Average) * 1000, 1)
    diskBusy = [math]::Round(($db | Measure-Object -Average).Average, 1)
    commit = [math]::Round(($cm | Measure-Object -Average).Average, 1)
    topCpu = @($by | Sort-Object cpu -Descending | Select-Object -First 6)
    topRam = @($by | Sort-Object ramMb -Descending | Select-Object -First 6)
  }
}
function PingTest($addr) {
  if (-not $addr) { return $null }
  $p = New-Object System.Net.NetworkInformation.Ping
  $ok = 0; $times = @()
  for ($i = 0; $i -lt 5; $i++) {
    try { $x = $p.Send($addr, 1500); if ($x.Status -eq 'Success') { $ok++; $times += [double]$x.RoundtripTime } } catch { }
  }
  $avg = $null; $max = $null
  if ($times.Count -gt 0) { $avg = [math]::Round(($times | Measure-Object -Average).Average, 1); $max = ($times | Measure-Object -Maximum).Maximum }
  [pscustomobject]@{ addr = $addr; sent = 5; ok = $ok; avg = $avg; max = $max }
}
Sec 'net' {
  $ad = @(Get-NetAdapter -Physical | ForEach-Object {
    [pscustomobject]@{ name = $_.Name; desc = $_.InterfaceDescription; status = "$($_.Status)"; speed = "$($_.LinkSpeed)"; media = "$($_.PhysicalMediaType)" }
  })
  $cfg = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway })
  $gw = $null; $dns = @()
  if ($cfg.Count -gt 0) { $gw = "$($cfg[0].IPv4DefaultGateway.NextHop)"; $dns = @($cfg[0].DNSServer.ServerAddresses | ForEach-Object { "$_" }) }
  $sig = $null
  $w = (netsh wlan show interfaces | Out-String)
  if ($w -match '(?m)^\s*(Signal|Sinal)\s*:\s*(\d+)%') { $sig = [int]$Matches[2] }
  $t0 = Get-Date; $dnsOk = $false
  try { $null = Resolve-DnsName 'www.microsoft.com' -Type A -ErrorAction Stop; $dnsOk = $true } catch { }
  $dnsMs = [math]::Round(((Get-Date) - $t0).TotalMilliseconds)
  [pscustomobject]@{ adapters = $ad; gateway = $gw; dns = $dns; wifiSignal = $sig; dnsOk = $dnsOk; dnsMs = $dnsMs
    pingGw = (PingTest $gw); pingNet1 = (PingTest '1.1.1.1'); pingNet2 = (PingTest '8.8.8.8') }
}
Sec 'update' {
  $s = Get-Service wuauserv
  [pscustomobject]@{ wuStatus = "$($s.Status)"; wuStart = "$($s.StartType)" }
}
Sec 'security' {
  $m = Get-MpComputerStatus
  $fw = @(Get-NetFirewallProfile | ForEach-Object { [pscustomobject]@{ name = "$($_.Name)"; on = "$($_.Enabled)" } })
  [pscustomobject]@{ av = "$($m.AntivirusEnabled)"; rtp = "$($m.RealTimeProtectionEnabled)"; sigAge = $m.AntivirusSignatureAge; quickAge = $m.QuickScanAge; fw = $fw }
}
SecL 'threats' {
  Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -gt $start } | Select-Object -First 10 | ForEach-Object {
    [pscustomobject]@{ t = (D $_.InitialDetectionTime); id = "$($_.ThreatID)" }
  }
}
$r['errors'] = @($script:errs)
$r['collected'] = (D (Get-Date))
$r | ConvertTo-Json -Depth 6 -Compress
"""


def collect():
    """Executa a coleta no Windows e devolve o dicionário bruto (JSON)."""
    if sys.platform != "win32":
        raise RuntimeError("O diagnóstico completo só funciona no Windows.")
    path = os.path.join(tempfile.gettempdir(), "pcmonitor-diag.ps1")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(PS_SCRIPT)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path],
                           capture_output=True, timeout=300, creationflags=flags)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    out = p.stdout.decode("utf-8", errors="replace")
    i, j = out.find("{"), out.rfind("}")
    if i < 0 or j <= i:
        err = p.stderr.decode("utf-8", errors="replace").strip()[:600]
        raise RuntimeError("O PowerShell não devolveu dados. " + err)
    return json.loads(out[i:j + 1])


# ====================================================================== análise
BUGCHECKS = {
    0x0A: ("IRQL_NOT_LESS_OR_EQUAL", "mem"), 0x1A: ("MEMORY_MANAGEMENT", "mem"),
    0x1E: ("KMODE_EXCEPTION_NOT_HANDLED", "mem"), 0x3B: ("SYSTEM_SERVICE_EXCEPTION", "mem"),
    0x4E: ("PFN_LIST_CORRUPT", "mem"), 0x50: ("PAGE_FAULT_IN_NONPAGED_AREA", "mem"),
    0x18: ("REFERENCE_BY_POINTER", "mem"), 0x19: ("BAD_POOL_HEADER", "mem"), 0xC2: ("BAD_POOL_CALLER", "mem"),
    0xDE: ("POOL_CORRUPTION_IN_FILE_AREA", "mem"), 0x139: ("KERNEL_SECURITY_CHECK_FAILURE", "mem"),
    0x13A: ("KERNEL_MODE_HEAP_CORRUPTION", "mem"), 0x189: ("BAD_OBJECT_HEADER", "mem"),
    0xD1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "driver"), 0x7E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "driver"),
    0x133: ("DPC_WATCHDOG_VIOLATION", "driver"), 0x9F: ("DRIVER_POWER_STATE_FAILURE", "driver"),
    0x116: ("VIDEO_TDR_FAILURE", "gpu"), 0x117: ("VIDEO_TDR_TIMEOUT_DETECTED", "gpu"),
    0x119: ("VIDEO_SCHEDULER_INTERNAL_ERROR", "gpu"), 0x141: ("VIDEO_ENGINE_TIMEOUT_DETECTED", "gpu"),
    0x7A: ("KERNEL_DATA_INPAGE_ERROR", "disk"), 0x77: ("KERNEL_STACK_INPAGE_ERROR", "disk"),
    0x24: ("NTFS_FILE_SYSTEM", "disk"), 0x154: ("UNEXPECTED_STORE_EXCEPTION", "disk"),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", "hw"), 0x101: ("CLOCK_WATCHDOG_TIMEOUT", "hw"),
    0x7F: ("UNEXPECTED_KERNEL_MODE_TRAP", "hw"), 0xEF: ("CRITICAL_PROCESS_DIED", "other"),
    0xF4: ("CRITICAL_OBJECT_TERMINATION", "other"),
}
CAT_NAMES = {"mem": "memória/corrupção", "driver": "driver", "gpu": "placa de vídeo", "disk": "disco",
             "hw": "hardware (CPU/placa)", "other": "outro"}

# Tipos de problema que o usuário pode escolher: (nome, etiquetas dos achados que importam)
SCENARIOS = {
    "geral": ("Check-up geral (tudo)", None),
    "lento": ("PC lento ou travando", {"perf", "mem", "disk", "startup", "thermal", "security"}),
    "quedas": ("Tela azul, reinícios ou desligamentos", {"crash", "power", "hw", "os", "thermal", "mem"}),
    "apps": ("Programas fechando ou não respondendo", {"apps", "mem", "gpu", "os"}),
    "calor": ("Superaquecimento ou ventoinhas barulhentas", {"thermal", "perf", "power"}),
    "disco": ("Disco cheio, lento ou com erros", {"disk", "startup"}),
    "rede": ("Internet lenta ou sem conexão", {"net"}),
    "video": ("Problemas de vídeo, tela ou jogos", {"gpu", "video"}),
    "update": ("Windows Update ou sistema corrompido", {"update", "os", "disk", "security"}),
}

HYP_NAMES = {
    "ram": "Memória RAM instável (EXPO/overclock ou pente defeituoso)",
    "gpu": "Driver ou hardware da placa de vídeo",
    "disk": "Disco ou controladora de armazenamento com defeito",
    "driver": "Driver ou software específico",
    "thermal": "Superaquecimento",
    "power": "Fonte de alimentação / energia elétrica",
    "hw": "CPU, placa-mãe ou barramento (erros WHEA)",
    "os": "Versão do Windows (build de testes ou atualização problemática)",
    "lowram": "Pouca memória livre ou muitos programas abertos",
    "bgload": "Algum processo consumindo muita CPU em segundo plano",
    "startup": "Muitos programas iniciando junto com o Windows",
    "diskslow": "Disco lento, ocupado ou quase cheio",
    "netlocal": "Problema na rede local (Wi-Fi, cabo ou roteador)",
    "netisp": "Problema no provedor de internet",
    "netdns": "Problema de DNS",
    "netadapter": "Adaptador de rede desconectado ou limitado",
    "security": "Proteção desativada ou possível ameaça",
    "update": "Windows Update com falhas ou serviço desativado",
}
HYP_TAGS = {
    "ram": {"crash", "mem", "apps", "perf"}, "gpu": {"gpu", "video", "crash", "apps"},
    "disk": {"disk", "crash", "perf"}, "driver": {"crash", "video", "apps"},
    "thermal": {"thermal", "crash", "perf", "power"}, "power": {"power", "crash"}, "hw": {"crash", "hw"},
    "os": {"os", "crash", "update", "apps"}, "lowram": {"perf", "mem"}, "bgload": {"perf", "thermal"},
    "startup": {"perf", "startup"}, "diskslow": {"disk", "perf"}, "netlocal": {"net"}, "netisp": {"net"},
    "netdns": {"net"}, "netadapter": {"net"}, "security": {"security", "perf"}, "update": {"update", "os"},
}
HYP_ADVICE = {
    "ram": ["Na BIOS, desative o EXPO/DOCP e deixe a memória no padrão. Use o PC normalmente por 2 a 3 dias.",
            "Rode o MemTest86 (pendrive), 4 passadas completas. Se der erro, teste um pente por vez, no mesmo slot.",
            "Atualize a BIOS da placa-mãe (melhora a compatibilidade de memória).",
            "Se ainda houver erros com a RAM no padrão, o pente está com defeito: acione a garantia."],
    "gpu": ["Reinstale o driver da placa de vídeo com o DDU (modo seguro) e use uma versão estável.",
            "Se houver GPU integrada e dedicada, confirme em qual delas o monitor está ligado.",
            "Confira cabos de energia da placa e o encaixe no slot PCIe; monitore a temperatura."],
    "disk": ["Faça backup agora dos dados importantes.",
             "Veja o SMART no CrystalDiskInfo e rode 'chkdsk /scan' (aba Ferramentas).",
             "Troque o cabo SATA/porta e teste o disco isolado."],
    "driver": ["Descubra o driver culpado abrindo os minidumps (C:\\Windows\\Minidump) no BlueScreenView ou WinDbg.",
               "Atualize chipset, rede, áudio e Bluetooth; remova softwares de overlay/otimização que injetam drivers."],
    "thermal": ["Limpe poeira, revise a pasta térmica e a curva das ventoinhas; garanta boa circulação de ar no gabinete.",
                "Deixe o widget com as temperaturas visível e veja o valor no momento do problema."],
    "power": ["Teste outra tomada/estabilizador/nobreak e confira os cabos da fonte.",
              "Quedas sem tela azul (reinício seco) sugerem energia ou travamento total de hardware."],
    "hw": ["Remova overclock/PBO/EXPO, atualize a BIOS e verifique temperatura e energia da CPU.",
           "Se persistir com tudo no padrão, pode ser defeito de CPU ou placa-mãe (garantia)."],
    "os": ["O Windows em canal Insider pode ter bugs de driver/kernel. Considere voltar para uma versão estável "
           "(pode exigir reinstalar, dependendo do canal e da build). Faça backup antes."],
    "lowram": ["Feche programas e abas que você não usa (veja os maiores consumidores no relatório).",
               "Se a RAM vive acima de 85%, considere adicionar mais memória.",
               "Reinicie o PC para liberar vazamentos de programas abertos há muito tempo."],
    "bgload": ["Abra o Gerenciador de Tarefas (Ctrl+Shift+Esc), ordene por CPU e veja o que mais consome.",
               "Confira se não é verificação do antivírus, indexação ou atualização em andamento (terminam sozinhas).",
               "Se o processo for desconhecido, pesquise o nome antes de encerrar e rode uma verificação do Defender."],
    "startup": ["Gerenciador de Tarefas > Inicializar: desative os programas que você não precisa ao ligar o PC.",
                "Programas desativados continuam funcionando quando você os abre manualmente."],
    "diskslow": ["Libere espaço: mantenha ao menos 15% do disco livre.",
                 "Se o Windows está em HDD, migrar para SSD é a maior melhora de velocidade possível.",
                 "Veja em Gerenciador de Tarefas > Desempenho > Disco qual programa está usando o disco."],
    "netlocal": ["Reinicie o roteador (tire da tomada por 30 s) e teste de novo.",
                 "No Wi-Fi: aproxime-se do roteador, use a banda de 5 GHz ou teste por cabo.",
                 "Troque o cabo de rede e a porta do roteador."],
    "netisp": ["O roteador responde mas a internet não: reinicie modem e roteador e confira as luzes do modem.",
               "Se continuar, é do provedor: ligue para o suporte informando horário e os testes feitos."],
    "netdns": ["Troque o DNS do adaptador para 1.1.1.1 e 8.8.8.8 (Configurações > Rede > propriedades do adaptador).",
               "Em um terminal, rode 'ipconfig /flushdns'."],
    "netadapter": ["Verifique se o cabo está conectado, o Wi-Fi ligado e o modo avião desativado.",
                   "Atualize ou reinstale o driver do adaptador no Gerenciador de Dispositivos."],
    "security": ["Ative a proteção em tempo real em Segurança do Windows e atualize as definições.",
                 "Rode uma verificação completa; se houver ameaça, siga a remoção sugerida."],
    "update": ["Reinicie o PC e rode o Windows Update; se falhar, use a solução de problemas do Windows Update.",
               "Rode DISM RestoreHealth e SFC (aba Ferramentas) e libere espaço no C: (15 GB ou mais)."],
}
SYSTEM_APPS = ("dwm.exe", "msmpeng.exe", "explorer.exe", "svchost.exe", "tiworker.exe", "taskmgr.exe",
               "startmenuexperiencehost.exe", "runtimebroker.exe", "searchhost.exe", "sihost.exe")
CRASH_CODES = {"c0000005": "acesso inválido à memória", "c0000374": "corrupção de heap", "c0000409": "falha de segurança/pilha"}

SEV_ORDER = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
SEV_LABEL = {"crit": "CRÍTICO", "warn": "ATENÇÃO", "info": "INFO", "ok": "OK"}


@dataclass
class Finding:
    sev: str
    title: str
    detail: str = ""
    advice: str = ""
    tags: set = field(default_factory=set)


@dataclass
class Report:
    created: str = ""
    scenario: str = "geral"
    system: list = field(default_factory=list)
    findings: list = field(default_factory=list)      # todos
    relevant: list = field(default_factory=list)      # no foco do cenário
    others: list = field(default_factory=list)        # fora do foco
    hypotheses: list = field(default_factory=list)    # (chave, nome, pontos, evidências, conselhos)
    confidence: str = "baixa"
    collection_errors: list = field(default_factory=list)

    def top(self):
        return self.hypotheses[0] if self.hypotheses else None

    def scenario_label(self):
        return SCENARIOS.get(self.scenario, SCENARIOS["geral"])[0]

    def to_text(self):
        L = [f"RELATÓRIO DE DIAGNÓSTICO — {self.created}", f"Foco: {self.scenario_label()}", ""]
        L += ["SISTEMA"] + [f"  {s}" for s in self.system] + [""]
        t = self.top()
        if t:
            L += [f"CAUSA MAIS PROVÁVEL (confiança {self.confidence}): {t[1]}", ""]
            L += ["  Evidências:"] + [f"   - {e}" for e in t[3]]
            L += ["  O que fazer:"] + [f"   {i}. {a}" for i, a in enumerate(t[4], 1)] + [""]
            others = [h for h in self.hypotheses[1:] if h[2] > 0]
            if others:
                L += ["OUTRAS HIPÓTESES"] + [f"  - {h[1]} (pontos: {h[2]})" for h in others] + [""]
        else:
            L += ["Nenhuma causa provável identificada para este foco.", ""]

        def dump(items):
            for f in items:
                L.append(f"[{SEV_LABEL[f.sev]}] {f.title}")
                if f.detail:
                    L.extend(f"    {ln}" for ln in f.detail.splitlines())
                if f.advice:
                    L.append(f"    → {f.advice}")
                L.append("")

        L += ["ACHADOS DO FOCO"]
        dump(self.relevant)
        if self.others:
            L += ["OUTROS ACHADOS (fora do foco)"]
            dump(self.others)
        if self.collection_errors:
            L += ["AVISOS DA COLETA"] + [f"  - {e}" for e in self.collection_errors]
        return "\n".join(L)


def as_list(x):
    if x is None or x == "":
        return []
    return x if isinstance(x, list) else [x]


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def hex_code(msg):
    m = re.search(r"0x([0-9a-fA-F]{1,16})", msg or "")
    return int(m.group(1), 16) if m else None


def fmt_dt(d):
    return d.strftime("%d/%m %H:%M") if d else "?"


def parse_mbps(s):
    m = re.search(r"([\d.,]+)\s*([GMK])bps", str(s or ""), re.I)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return v * {"G": 1000, "M": 1, "K": 0.001}[m.group(2).upper()]


def _loss(p):
    return 100.0 * (1 - float(p.get("ok") or 0) / float(p.get("sent") or 1))


def analyze(raw, history_info=None, milestone=None, now=None, scenario="geral", recent=None):
    now = now or dt.datetime.now()
    scenario = scenario if scenario in SCENARIOS else "geral"
    rep = Report(created=now.strftime("%d/%m/%Y %H:%M"), scenario=scenario)
    rep.collection_errors = [str(e) for e in as_list(raw.get("errors"))]
    score = collections.Counter()
    evid = collections.defaultdict(list)
    cur = {"tags": set()}

    def T(*tags):
        cur["tags"] = set(tags)

    def F(f):
        f.tags = set(cur["tags"])
        rep.findings.append(f)

    def add(key, pts, text):
        score[key] += pts
        evid[key].append(text)

    # ---------------------------------------------------------------- sistema
    osd, board = raw.get("os") or {}, raw.get("board") or {}
    ram = as_list(raw.get("ram"))
    build = str(osd.get("build") or "")
    if board:
        rep.system.append(f"{board.get('maker', '')} {board.get('model', '')} (placa {board.get('board', '')})")
        rep.system.append(f"CPU: {board.get('cpu', '')} — {board.get('cores', '?')} núcleos / {board.get('threads', '?')} threads")
        bd = parse_ts(board.get("biosDate"))
        rep.system.append(f"BIOS: {board.get('biosVersion', '?')} ({bd.strftime('%d/%m/%Y') if bd else 'data desconhecida'})")
    if osd:
        rep.system.append(" ".join(x for x in (str(osd.get("caption") or "Windows"), str(osd.get("display") or "")) if x)
                          + f" — build {build}" + (f".{osd['ubr']}" if osd.get("ubr") else ""))
    if ram:
        tot = sum(float(m.get("gb") or 0) for m in ram)
        rep.system.append(f"RAM: {tot:.0f} GB em {len(ram)} pente(s): " + "; ".join(
            f"{m.get('maker', '')} {m.get('part', '')} {m.get('gb', '')}GB" for m in ram))
    for g in as_list(raw.get("gpus")):
        rep.system.append(f"GPU: {g.get('name')} (driver {g.get('driver')})")

    # ---------------------------------------------------------------- quedas inesperadas
    T("crash", "power")
    power = sorted(t for t in (parse_ts(e.get("t")) for e in as_list(raw.get("ev_power"))) if t)
    bug_events = [(parse_ts(e.get("t")), hex_code(e.get("m"))) for e in as_list(raw.get("ev_bugcheck"))]
    bug_times = [t for t, _c in bug_events if t]
    n30 = len(power)
    n7 = sum(1 for t in power if t >= now - dt.timedelta(days=7))
    unmatched = [t for t in power if not any(-10 <= (b - t).total_seconds() <= 180 for b in bug_times)]
    if n30 == 0:
        F(Finding("ok", "Nenhuma queda inesperada nos últimos 30 dias"))
    else:
        sev = "crit" if (n7 >= 3 or n30 >= 8) else "warn"
        det = [f"{n30} em 30 dias, {n7} nos últimos 7. A mais recente: {fmt_dt(power[-1])}."]
        if n30 >= 2:
            span_h = (power[-1] - power[0]).total_seconds() / 3600
            det.append(f"Entre a primeira e a última, uma queda a cada ~{span_h / (n30 - 1):.1f} h em média.")
        det.append(f"{n30 - len(unmatched)} terminaram em tela azul com código registrado; "
                   f"{len(unmatched)} sem tela azul registrada (travamento total, botão/reset ou perda de energia).")
        F(Finding(sev, f"{n30} quedas/reinícios inesperados em 30 dias ({n7} na última semana)", "\n".join(det),
                  "Anote a data de cada mudança que fizer (BIOS, driver, Windows) com 'Marcar mudança' para comparar."))
        if n30 >= 3 and len(unmatched) / n30 >= 0.4:
            add("power", 12, f"{len(unmatched)} de {n30} quedas sem tela azul registrada (parece reinício seco)")

    # ---------------------------------------------------------------- telas azuis
    T("crash", "mem")
    codes = collections.Counter(c for _t, c in bug_events if c is not None)
    if codes:
        info = {c: BUGCHECKS.get(c, (f"código 0x{c:X}", "other")) for c in codes}
        mem_distinct = sum(1 for c in codes if info[c][1] == "mem")
        total = sum(codes.values())
        lines = [f"0x{c:X}  {info[c][0]}  ×{n}  ({CAT_NAMES[info[c][1]]})" for c, n in codes.most_common(14)]
        if mem_distinct >= 4:
            sev = "crit"
            title = (f"{len(codes)} códigos diferentes de tela azul em {total} ocorrências, "
                     f"{mem_distinct} deles ligados a memória")
            adv = ("Muitos códigos diferentes sem causa única é o padrão clássico de RAM/overclock instável "
                   "(ou driver que corrompe memória).")
            add("ram", 12, f"{mem_distinct} códigos de tela azul diferentes ligados a memória")
        else:
            sev = "warn"
            title = f"{total} telas azuis registradas ({len(codes)} códigos)"
            adv = ""
        F(Finding(sev, title, "\n".join(lines), adv))
        top_c, top_n = codes.most_common(1)[0]
        cat = info[top_c][1]
        if total >= 3 and top_n / total >= 0.5 and cat in ("gpu", "driver", "disk", "hw"):
            add(cat, 15, f"{top_n} de {total} telas azuis são {info[top_c][0]} (0x{top_c:X})")
            F(Finding("warn", f"Um código domina: {info[top_c][0]} (0x{top_c:X}) em {top_n} de {total}",
                      f"Categoria: {CAT_NAMES[cat]}.", ""))
    elif n30:
        F(Finding("info", "Há quedas, mas nenhum código de tela azul foi lido do registro",
                  "Pode ser reinício seco (energia/hardware) ou o registro foi limpo."))

    # ---------------------------------------------------------------- teste de memória do Windows
    T("crash", "mem", "apps", "perf")
    bad_re = re.compile(r"defeit|defective|bad memory|danific|défectu|defectu", re.I)
    for e in as_list(raw.get("ev_memdiag")):
        msg, eid, when = e.get("m", ""), e.get("id"), parse_ts(e.get("t"))
        if bad_re.search(msg) or eid == 1201:
            F(Finding("crit", f"O Windows detectou memória defeituosa ({fmt_dt(when)})", msg[:300],
                      "Isso prova erro físico/instabilidade na RAM. Faça o teste com EXPO desativado e o MemTest86."))
            add("ram", 15, f"Teste de memória do Windows encontrou regiões defeituosas ({fmt_dt(when)})")
        elif eid == 1101:
            F(Finding("ok", f"Teste de memória do Windows sem erros ({fmt_dt(when)})",
                      "O teste rápido do Windows pode não achar erros que o MemTest86 acha."))

    # ---------------------------------------------------------------- RAM / EXPO
    T("crash", "mem", "apps")
    if ram:
        speeds = [int(m.get("configured") or m.get("rated") or 0) for m in ram]
        ddr5 = any(m.get("type") == 34 for m in ram) or any(s >= 4800 for s in speeds)
        top_speed = max(speeds) if speeds else 0
        known_cfg = any(m.get("configured") for m in ram)
        if ddr5 and top_speed >= 5600:
            origin = "velocidade configurada" if known_cfg else "velocidade nominal do pente (a configurada não foi lida)"
            F(Finding("warn", f"Memória rodando/anunciada a {top_speed} MT/s ({origin})",
                      "Acima do padrão DDR5 (≈4800 a 5600), o perfil EXPO/XMP costuma estar ativo. "
                      "Em AM5, esses perfis são uma causa comum de instabilidade.",
                      "Teste com EXPO/DOCP desativado por 2 a 3 dias."))
            if known_cfg:
                add("ram", 8, f"Memória configurada a {top_speed} MT/s (perfil EXPO/XMP provável)")
        if len({(m.get("part"), m.get("gb")) for m in ram}) > 1:
            F(Finding("warn", "Pentes de RAM diferentes entre si",
                      "; ".join(f"{m.get('slot')}: {m.get('part')} {m.get('gb')}GB" for m in ram),
                      "Pentes de modelos diferentes aumentam o risco de instabilidade."))

    # ---------------------------------------------------------------- WHEA
    T("crash", "hw")
    whea = as_list(raw.get("ev_whea"))
    if whea:
        F(Finding("crit", f"{len(whea)} erros de hardware reportados pelo Windows (WHEA)",
                  "\n".join(f"{fmt_dt(parse_ts(e.get('t')))}: {e.get('m', '')[:160]}" for e in whea[:4]),
                  "Erros WHEA indicam CPU, memória ou barramento PCIe reportando falha ao Windows."))
        add("hw", 20, f"{len(whea)} eventos WHEA")
    else:
        F(Finding("ok", "Nenhum erro de hardware WHEA registrado"))

    # ---------------------------------------------------------------- discos
    T("disk", "crash", "perf", "update")
    bad_disk = False
    for d in as_list(raw.get("disks")):
        name = d.get("name", "disco")
        issues = []
        if d.get("health") not in (None, "Healthy"):
            issues.append(f"saúde: {d.get('health')}")
            bad_disk = True
        if d.get("temp") is not None and d["temp"] >= 65:
            issues.append(f"temperatura {d['temp']} °C")
        if d.get("wear") is not None and d["wear"] >= 80:
            issues.append(f"desgaste {d['wear']}%")
        if (d.get("readErr") or 0) > 0 or (d.get("writeErr") or 0) > 0:
            issues.append(f"erros de leitura/escrita: {d.get('readErr') or 0}/{d.get('writeErr') or 0}")
            bad_disk = True
        if issues:
            F(Finding("warn", f"Disco {name}: " + ", ".join(issues), "", "Faça backup e confira o SMART."))
    if as_list(raw.get("disks")) and not bad_disk:
        F(Finding("ok", "Discos reportam saúde normal (Healthy) e sem erros de leitura/escrita"))
    low_c = False
    for v in as_list(raw.get("volumes")):
        gb, free = float(v.get("gb") or 0), float(v.get("freeGb") or 0)
        if gb >= 20 and free / gb < 0.10:
            F(Finding("warn", f"Pouco espaço livre em {v.get('letter')}: {free:.0f} GB de {gb:.0f} GB ({free / gb * 100:.0f}%)",
                      "", "Menos de 10% livre prejudica o desempenho e a atualização do Windows."))
            add("diskslow", 8, f"{v.get('letter')}: com apenas {free / gb * 100:.0f}% livre")
        if str(v.get("letter")).upper() == "C" and free < 15:
            low_c = True
    dev = as_list(raw.get("ev_disk"))
    hard = [e for e in dev if e.get("id") in {7, 11, 15, 51, 55, 98, 129, 153, 157}]
    if hard:
        c = collections.Counter(f"{e.get('p')} #{e.get('id')}" for e in hard)
        F(Finding("crit", f"{len(hard)} eventos de erro de disco no registro",
                  "\n".join(f"{k} ×{n}" for k, n in c.most_common(6)),
                  "IDs como 7, 11, 51, 55 e 153 indicam bad block, falha de controladora ou erro de paginação."))
        add("disk", 20, f"{len(hard)} eventos de erro de disco")
    if bad_disk:
        add("disk", 20, "um disco reporta saúde ruim ou erros de leitura/escrita")
    T("crash")
    dumps = as_list(raw.get("ev_dump"))
    if dumps:
        F(Finding("info", f"Em {len(dumps)} quedas o Windows não conseguiu gravar o dump completo",
                  "Costuma acontecer quando o sistema já está muito instável no momento da falha."))

    # ---------------------------------------------------------------- travamentos de programas
    T("apps", "mem", "crash")
    apps = as_list(raw.get("ev_apps"))
    if apps:
        by_app = collections.Counter(a.get("app", "?") for a in apps)
        mem_apps = {a.get("app") for a in apps if str(a.get("code", "")).lower() in CRASH_CODES}
        heap_apps = {a.get("app") for a in apps if str(a.get("code", "")).lower() == "c0000374"}
        sys_apps = {a for a in mem_apps if str(a).lower().startswith(SYSTEM_APPS)}
        codes_n = collections.Counter(str(a.get("code", "")).lower() for a in apps)
        det = ["Mais frequentes: " + ", ".join(f"{k} ×{n}" for k, n in by_app.most_common(6)),
               "Códigos: " + ", ".join(f"{k} ({CRASH_CODES.get(k, 'outro')}) ×{n}" for k, n in codes_n.most_common(4))]
        top_app, top_n_app = by_app.most_common(1)[0]
        if len(mem_apps) >= 6 and len(sys_apps) >= 2:
            F(Finding("crit", f"{len(mem_apps)} programas diferentes travaram com erros de memória, incluindo {len(sys_apps)} do próprio Windows",
                      "\n".join(det),
                      "Falhas simultâneas em programas sem relação (inclusive dwm, Defender, svchost) apontam para memória/hardware, não para um app."))
            add("ram", 10, f"{len(mem_apps)} programas diferentes com falha de memória (acesso inválido/heap), incluindo processos do Windows")
        elif top_n_app >= 5 and top_n_app / len(apps) >= 0.5:
            F(Finding("warn", f"{top_app} concentra {top_n_app} de {len(apps)} travamentos",
                      "\n".join(det), f"O problema é provavelmente só desse programa: atualize, repare ou reinstale {top_app}."))
        else:
            F(Finding("warn" if len(apps) >= 10 else "info", f"{len(apps)} travamentos de programas em 30 dias", "\n".join(det)))
        if len(heap_apps) >= 3:
            add("ram", 8, f"corrupção de heap (c0000374) em {len(heap_apps)} programas diferentes")
        gpu_mods = [a for a in apps if re.search(r"nvlddmkm|nvwgf2um|nvd3dum|atiumd|amdxx|d3d11|d3d12", a.get("module", ""), re.I)]
        if len(gpu_mods) >= 3:
            add("gpu", 6, f"{len(gpu_mods)} travamentos em módulos de vídeo")
    else:
        F(Finding("ok", "Nenhum travamento de programa registrado nos últimos 30 dias"))
    T("apps", "perf")
    hangs = as_list(raw.get("ev_hangs"))
    if hangs:
        hc = collections.Counter(h.get("app", "?") for h in hangs)
        F(Finding("warn" if len(hangs) >= 10 else "info", f"{len(hangs)} vezes um programa deixou de responder",
                  "Mais frequentes: " + ", ".join(f"{k} ×{n}" for k, n in hc.most_common(6)),
                  "Programas que congelam com frequência costumam estar sobrecarregados (CPU/RAM/disco) ou com bug."))

    # ---------------------------------------------------------------- GPU, memória, drivers
    T("gpu", "video", "apps", "crash")
    gev = as_list(raw.get("ev_gpu"))
    tdr = [e for e in gev if e.get("id") == 4101 or re.search(r"timeout|TDR|reset", e.get("m", ""), re.I)]
    if tdr:
        F(Finding("warn", f"{len(tdr)} eventos de reinício do driver de vídeo (TDR)", "", "Reinstale o driver da GPU com DDU."))
        add("gpu", 8, f"{len(tdr)} eventos de timeout/reset do driver de vídeo")
    elif as_list(raw.get("gpus")):
        F(Finding("ok", "Nenhum reinício do driver de vídeo registrado"))
    if sum(1 for c in codes if c in (0x116, 0x117, 0x119, 0x141)) >= 2:
        add("gpu", 15, "mais de uma tela azul de vídeo (TDR)")
    gpus = as_list(raw.get("gpus"))
    if len(gpus) >= 2:
        F(Finding("info", "Este PC tem mais de uma placa de vídeo (integrada + dedicada)",
                  "; ".join(str(g.get("name")) for g in gpus),
                  "Confirme que o monitor está ligado na saída da placa DEDICADA (não na da placa-mãe) para jogos e desempenho."))
    for g in gpus:
        d = parse_ts(g.get("date"))
        if d and (now - d).days > 540 and "microsoft basic" not in str(g.get("name", "")).lower():
            F(Finding("info", f"Driver de vídeo antigo: {g.get('name')} ({d.strftime('%m/%Y')})", "",
                      "Considere atualizar pelo site da AMD/NVIDIA."))
    T("perf", "mem", "apps")
    if as_list(raw.get("ev_exhaust")):
        n = len(as_list(raw.get("ev_exhaust")))
        F(Finding("warn", f"O Windows registrou {n} vezes falta de memória", "", "Feche programas pesados ou aumente o arquivo de paginação."))
        add("lowram", 10, f"esgotamento de memória registrado {n} vez(es)")
    T("video", "driver", "crash", "perf")
    pnp = as_list(raw.get("pnp"))
    if pnp:
        F(Finding("warn", f"{len(pnp)} dispositivo(s) com problema no Gerenciador de Dispositivos",
                  "\n".join(f"{p.get('cls')}: {p.get('name')} ({p.get('status')}, {p.get('problem')})" for p in pnp[:6]),
                  "Atualize ou reinstale o driver desses dispositivos."))
        add("driver", 4, f"{len(pnp)} dispositivos com problema")

    # ---------------------------------------------------------------- desempenho agora
    perf = raw.get("perf") or {}
    T("perf", "mem")
    tot_kb, free_kb = float(osd.get("ramKb") or 0), float(osd.get("ramFreeKb") or 0)
    top_ram = [p for p in as_list(perf.get("topRam"))]
    top_cpu = [p for p in as_list(perf.get("topCpu")) if str(p.get("name", "")).lower() not in ("idle", "_total")]
    if tot_kb > 0:
        used_pct = (1 - free_kb / tot_kb) * 100
        used_gb, tot_gb = (tot_kb - free_kb) / 1048576, tot_kb / 1048576
        det = "Maiores consumidores: " + ", ".join(f"{p.get('name')} ({float(p.get('ramMb') or 0):.0f} MB)" for p in top_ram[:5]) if top_ram else ""
        commit = perf.get("commit")
        if commit is not None:
            det += (("\n" if det else "") + f"Memória comprometida (commit): {float(commit):.0f}%")
        sev = "crit" if used_pct >= 92 else "warn" if used_pct >= 85 else "ok"
        F(Finding(sev, f"Memória RAM em uso: {used_pct:.0f}% ({used_gb:.1f} de {tot_gb:.1f} GB)", det,
                  "Feche programas pesados ou reinicie o PC." if sev != "ok" else ""))
        if used_pct >= 92:
            add("lowram", 18, f"RAM em {used_pct:.0f}% de uso")
        elif used_pct >= 85:
            add("lowram", 12, f"RAM em {used_pct:.0f}% de uso")
        if commit is not None and float(commit) >= 90:
            add("lowram", 8, f"memória comprometida em {float(commit):.0f}%")
    T("perf", "thermal")
    if perf.get("cpuAvg") is not None:
        cpu = float(perf["cpuAvg"])
        det = ("Maiores consumidores de CPU agora: " + ", ".join(
            f"{p.get('name')} ({float(p.get('cpu') or 0):.0f}%)" for p in top_cpu[:5] if float(p.get("cpu") or 0) >= 1)) if top_cpu else ""
        sev = "crit" if cpu >= 90 else "warn" if cpu >= 75 else "ok"
        F(Finding(sev, f"CPU em uso médio de {cpu:.0f}% durante a coleta", det,
                  "Veja no Gerenciador de Tarefas o que está consumindo." if sev != "ok" else ""))
        if cpu >= 85:
            add("bgload", 15, f"CPU em {cpu:.0f}% de uso médio")
        elif cpu >= 70:
            add("bgload", 8, f"CPU em {cpu:.0f}% de uso médio")
        if top_cpu and float(top_cpu[0].get("cpu") or 0) >= 25:
            add("bgload", 6, f"{top_cpu[0].get('name')} usando {float(top_cpu[0]['cpu']):.0f}% da CPU")
    T("disk", "perf")
    if perf.get("diskQueue") is not None:
        q, lat, busy = float(perf["diskQueue"]), float(perf.get("diskLatMs") or 0), float(perf.get("diskBusy") or 0)
        if q >= 2 or lat >= 30 or busy >= 90:
            F(Finding("warn", "Disco ocupado ou lento durante a coleta",
                      f"Fila média: {q:.1f}; latência: {lat:.0f} ms; tempo ocupado: {busy:.0f}%.",
                      "Veja no Gerenciador de Tarefas qual programa usa o disco; HDD costuma ser o gargalo."))
            add("diskslow", 15, f"disco ocupado (fila {q:.1f}, latência {lat:.0f} ms, {busy:.0f}% ocupado)")
        else:
            F(Finding("ok", f"Disco sem sobrecarga (latência {lat:.0f} ms, ocupado {busy:.0f}%)"))
    T("perf", "update")
    boot = parse_ts(osd.get("boot"))
    if boot and (now - boot).days >= 14:
        F(Finding("info", f"O PC está ligado há {(now - boot).days} dias sem reiniciar", "",
                  "Reiniciar libera memória e conclui atualizações pendentes."))
    T("startup", "perf")
    st = as_list(raw.get("startup"))
    if st:
        n = len(st)
        sev = "warn" if n >= 15 else "info" if n >= 8 else "ok"
        F(Finding(sev, f"{n} programas iniciam com o Windows",
                  ", ".join(sorted({str(s.get('name')) for s in st})[:14]),
                  "Desative os desnecessários em Gerenciador de Tarefas > Inicializar." if sev != "ok" else ""))
        if n >= 20:
            add("startup", 10, f"{n} programas na inicialização")
        elif n >= 12:
            add("startup", 6, f"{n} programas na inicialização")

    # ---------------------------------------------------------------- temperatura recente (monitor)
    T("thermal", "perf", "power")
    if recent and recent.get("rows", 0) >= 30:
        cpu_t, gpu_t = recent.get("cpu.temp"), recent.get("nv0.temp")
        hours = recent.get("rows", 0) * recent.get("interval_s", 5) / 3600
        for label, t, warn, crit, pts in (("CPU", cpu_t, 88, 95, (14, 25)), ("GPU", gpu_t, 85, 92, (10, 20))):
            if not t:
                continue
            sev = "crit" if t["max"] >= crit else "warn" if t["max"] >= warn else "ok"
            F(Finding(sev, f"Temperatura da {label}: máx. {t['max']:.0f} °C, média {t['avg']:.0f} °C (últimas ~{hours:.0f} h)",
                      "Baseado nas leituras gravadas pelo monitor.",
                      "Verifique limpeza, pasta térmica e ventoinhas." if sev != "ok" else ""))
            if sev != "ok":
                add("thermal", pts[1] if sev == "crit" else pts[0], f"{label} chegou a {t['max']:.0f} °C")
    else:
        F(Finding("info", "Sem dados suficientes de temperatura do monitor",
                  "Deixe o monitor rodando algumas horas (inclusive durante o problema) para avaliar o calor.", ""))

    # ---------------------------------------------------------------- segurança
    T("security", "perf")
    sec = raw.get("security") or {}
    if sec:
        probs = []
        if str(sec.get("av")).lower() not in ("true", ""):
            probs.append("antivírus do Windows desativado")
        if str(sec.get("rtp")).lower() not in ("true", ""):
            probs.append("proteção em tempo real desativada")
        if sec.get("sigAge") is not None and float(sec["sigAge"]) >= 7:
            probs.append(f"definições de vírus com {float(sec['sigAge']):.0f} dias")
        off = [f.get("name") for f in as_list(sec.get("fw")) if str(f.get("on")).lower() == "false"]
        if off:
            probs.append("firewall desativado em: " + ", ".join(off))
        if probs:
            F(Finding("warn", "Proteção do Windows com pendências", "; ".join(probs),
                      "Se você usa outro antivírus, isso pode ser normal; senão, ative a proteção em Segurança do Windows."))
            add("security", 8, "; ".join(probs))
        else:
            F(Finding("ok", "Antivírus e firewall do Windows ativos e atualizados"))
    threats = as_list(raw.get("threats"))
    if threats:
        F(Finding("crit", f"O Defender detectou {len(threats)} ameaça(s) nos últimos 30 dias",
                  ", ".join(f"{fmt_dt(parse_ts(t.get('t')))} (ID {t.get('id')})" for t in threats[:5]),
                  "Abra Segurança do Windows > Proteção contra vírus > Histórico de proteção e remova o que estiver pendente."))
        add("security", 15, f"{len(threats)} detecção(ões) de ameaça")

    # ---------------------------------------------------------------- Windows e BIOS
    T("os", "crash", "update", "apps")
    ring = str(osd.get("ring") or "").strip().lower()
    insider = str(osd.get("flight") or "").strip().lower() in ("1", "true") or ring in (
        "dev", "beta", "canary", "releasepreview", "rp", "wis", "wif", "external", "internal")
    try:
        bnum = int(build)
    except ValueError:
        bnum = 0
    if insider:
        F(Finding("warn", f"Windows em canal Insider ({osd.get('ring') or osd.get('branch') or 'teste'}) — build {build}",
                  "Builds de teste podem trazer bugs de kernel e drivers.",
                  "Se os problemas continuarem depois de corrigir o resto, considere uma versão estável."))
        add("os", 8, f"Windows Insider (build {build})")
    elif bnum > 26200:
        F(Finding("info", f"Build {build} é mais nova que as versões estáveis que conheço (26100/26200)",
                  "Pode ser uma build de pré-lançamento. Confira em 'winver' e em Windows Update.", ""))
        add("os", 4, f"build {build} possivelmente de pré-lançamento")
    T("crash")
    bd = parse_ts(board.get("biosDate")) if board else None
    if bd and (now - bd).days > 365:
        F(Finding("info", f"BIOS com mais de 1 ano ({board.get('biosVersion')}, {bd.strftime('%m/%Y')})", "",
                  "Veja no site do fabricante se há versão nova; atualizações costumam melhorar a compatibilidade de memória."))
    elif board:
        F(Finding("info", f"BIOS {board.get('biosVersion')} ({bd.strftime('%d/%m/%Y') if bd else '?'})", "", ""))
    T("crash", "perf")
    pw = raw.get("power") or {}
    if pw.get("fastStartup") == 1:
        F(Finding("info", "Inicialização rápida do Windows está ativada", "Ela mantém o estado do kernel/drivers entre desligamentos.",
                  "Para testes de estabilidade, desative (Opções de energia > Escolher a função dos botões)."))

    # ---------------------------------------------------------------- Windows Update
    T("update")
    upd = raw.get("update") or {}
    if str(upd.get("wuStart")).lower() == "disabled":
        F(Finding("warn", "O serviço do Windows Update está desativado", "", "Ative o serviço 'Windows Update' em services.msc."))
        add("update", 10, "serviço Windows Update desativado")
    uerr = as_list(raw.get("ev_update"))
    if len(uerr) >= 3:
        F(Finding("warn", f"{len(uerr)} falhas do Windows Update nos últimos 30 dias",
                  "\n".join(f"{fmt_dt(parse_ts(e.get('t')))}: {e.get('m', '')[:140]}" for e in uerr[:3]),
                  "Use a solução de problemas do Windows Update e rode DISM/SFC."))
        add("update", 12, f"{len(uerr)} falhas de atualização")
    if low_c:
        F(Finding("warn", "Menos de 15 GB livres no disco C:", "", "O Windows Update precisa de espaço livre para instalar."))
        add("update", 6, "pouco espaço livre no C:")
    if pw.get("pendingReboot"):
        F(Finding("info", "Há reinicialização pendente de atualização", "", "Reinicie o PC."))
        add("update", 3, "reinicialização pendente")
    hf = [parse_ts(h.get("on")) for h in as_list(raw.get("hotfix"))]
    hf = [h for h in hf if h]
    if hf and (now - max(hf)).days > 120:
        F(Finding("info", f"Última atualização instalada há {(now - max(hf)).days} dias", "", "Rode o Windows Update."))

    # ---------------------------------------------------------------- rede
    T("net")
    net = raw.get("net") or {}
    if net:
        ads = as_list(net.get("adapters"))
        up = [a for a in ads if str(a.get("status")).lower() == "up"]
        if ads and not up:
            F(Finding("crit", "Nenhum adaptador de rede está conectado", "; ".join(f"{a.get('name')}: {a.get('status')}" for a in ads),
                      "Confira o cabo, o Wi-Fi e o modo avião."))
            add("netadapter", 25, "nenhum adaptador de rede conectado")
        for a in up:
            sp = parse_mbps(a.get("speed"))
            if "802.3" in str(a.get("media")) and sp and sp <= 100:
                F(Finding("warn", f"Cabo de rede negociou só {sp:.0f} Mbps ({a.get('name')})", "",
                          "Cabo ruim ou porta antiga: troque o cabo (Cat5e/Cat6)."))
                add("netlocal", 8, f"cabo a {sp:.0f} Mbps")
        sig = net.get("wifiSignal")
        if sig is not None:
            if sig < 40:
                F(Finding("warn", f"Sinal do Wi-Fi fraco ({sig}%)", "", "Aproxime-se do roteador, use 5 GHz ou cabo."))
                add("netlocal", 12, f"sinal Wi-Fi em {sig}%")
            elif sig < 60:
                F(Finding("info", f"Sinal do Wi-Fi mediano ({sig}%)"))
        gw, g = net.get("gateway"), net.get("pingGw")
        p1, p2 = net.get("pingNet1"), net.get("pingNet2")
        if up and not gw:
            F(Finding("warn", "O PC não recebeu gateway/IP da rede", "", "Reinicie o roteador e renove o IP (ipconfig /renew)."))
            add("netadapter", 15, "sem gateway padrão")
        gw_ok = bool(g) and g.get("ok", 0) > 0
        if g and g.get("ok", 0) == 0 and up:
            F(Finding("crit", f"O roteador ({gw}) não responde", "", "Reinicie o roteador e confira cabo/Wi-Fi."))
            add("netlocal", 25, f"roteador {gw} sem resposta")
        elif g and _loss(g) >= 20:
            F(Finding("warn", f"Perda de pacotes até o roteador ({_loss(g):.0f}%)", "", "Wi-Fi com interferência ou cabo ruim."))
            add("netlocal", 15, f"{_loss(g):.0f}% de perda até o roteador")
        if g and g.get("avg") and g["avg"] > 30:
            F(Finding("warn", f"Latência alta até o roteador ({g['avg']:.0f} ms)", "", "Em rede local deveria ficar abaixo de 10 ms."))
            add("netlocal", 8, f"latência de {g['avg']:.0f} ms até o roteador")
        inet = [p for p in (p1, p2) if p]
        inet_ok = any(p.get("ok", 0) > 0 for p in inet)
        if inet and not inet_ok and gw_ok:
            F(Finding("crit", "Sem acesso à internet (o roteador responde)", "Os testes para 1.1.1.1 e 8.8.8.8 falharam.",
                      "Reinicie modem e roteador; se continuar, é o provedor."))
            add("netisp", 25, "roteador responde, mas 1.1.1.1 e 8.8.8.8 não")
        elif inet_ok:
            loss = max(_loss(p) for p in inet)
            lat = [p["avg"] for p in inet if p.get("avg")]
            lat_avg = sum(lat) / len(lat) if lat else 0
            if loss >= 20:
                F(Finding("warn", f"Perda de pacotes para a internet ({loss:.0f}%)", "", "Instabilidade no provedor ou no Wi-Fi."))
                add("netisp", 12, f"{loss:.0f}% de perda para a internet")
            elif lat_avg > 120:
                F(Finding("warn", f"Latência alta para a internet ({lat_avg:.0f} ms)", "", "Pode ser congestionamento ou Wi-Fi ruim."))
                add("netisp", 8, f"latência de {lat_avg:.0f} ms")
            else:
                F(Finding("ok", f"Internet respondendo (latência média {lat_avg:.0f} ms, perda {loss:.0f}%)"))
        if inet_ok and net.get("dnsOk") is False:
            F(Finding("crit", "O DNS não está resolvendo nomes (sites não abrem, mas há conexão)", "",
                      "Troque o DNS para 1.1.1.1 e 8.8.8.8."))
            add("netdns", 25, "ping funciona, mas a resolução de nomes falha")
        elif net.get("dnsOk") and (net.get("dnsMs") or 0) > 300:
            F(Finding("warn", f"DNS lento ({net['dnsMs']} ms)", "", "Troque para 1.1.1.1 / 8.8.8.8."))
            add("netdns", 8, f"DNS levou {net['dnsMs']} ms")
        elif net.get("dnsOk"):
            F(Finding("ok", f"DNS funcionando ({net.get('dnsMs')} ms)"))

    # ---------------------------------------------------------------- histórico do monitor antes das quedas
    T("thermal", "crash", "power")
    hist = history_info or []
    if hist:
        temps, rams = [], []
        for h in hist:
            v = h.get("values", {})
            temps += [v[k] for k in ("cpu.temp", "nv0.temp") if v.get(k) is not None]
            if v.get("ram.pct") is not None:
                rams.append(v["ram.pct"])
        if temps:
            hot = max(temps)
            if hot >= 90:
                F(Finding("crit", f"Antes de uma queda o monitor registrou {hot:.0f} °C",
                          f"Baseado em {len(hist)} sessões encerradas de forma inesperada.", "Verifique refrigeração."))
                add("thermal", 20, f"temperatura de {hot:.0f} °C registrada logo antes de uma queda")
            else:
                F(Finding("ok", f"Temperaturas normais antes das quedas (máx. {hot:.0f} °C nos últimos registros)",
                          f"Baseado em {len(hist)} sessões do monitor. Superaquecimento é improvável."))
        if rams:
            mx = max(rams)
            F(Finding("ok" if mx < 85 else "warn", f"RAM em uso antes das quedas: máx. {mx:.0f}%",
                      "Falta de memória é improvável." if mx < 85 else "Uso alto de memória no momento da queda."))

    # ---------------------------------------------------------------- marco de comparação
    T("crash", "power", "mem")
    if milestone and milestone.get("ts"):
        mt = parse_ts(milestone["ts"])
        if mt:
            hours = (now - mt).total_seconds() / 3600
            after = [t for t in power if t >= mt]
            note = milestone.get("note") or "mudança"
            if not after and hours >= 24:
                F(Finding("ok", f"Sem quedas há {hours:.0f} h desde '{note}' ({fmt_dt(mt)})",
                          "Bom sinal: continue acompanhando por alguns dias para confirmar.", ""))
            else:
                before = [t for t in power if mt - dt.timedelta(days=7) <= t < mt]
                F(Finding("info" if not after else "warn",
                          f"Desde '{note}' ({fmt_dt(mt)}): {len(after)} queda(s) em {hours:.0f} h",
                          f"Nos 7 dias antes da mudança: {len(before)} queda(s) (≈ {len(before) / 168 * 24:.1f}/dia). "
                          f"Depois: ≈ {len(after) / max(hours, 1) * 24:.1f}/dia.", ""))

    # ---------------------------------------------------------------- foco do cenário e hipóteses
    focus = SCENARIOS[scenario][1]
    for f in rep.findings:
        f.tags = set(f.tags)
    rep.findings.sort(key=lambda f: SEV_ORDER[f.sev])
    if focus is None:
        rep.relevant, rep.others = list(rep.findings), []
    else:
        rep.relevant = [f for f in rep.findings if f.tags & focus]
        rep.others = [f for f in rep.findings if not (f.tags & focus)]
    ranked = sorted(((k, HYP_NAMES[k], score[k], evid[k], HYP_ADVICE[k]) for k in HYP_NAMES
                     if score[k] > 0 and (focus is None or HYP_TAGS[k] & focus)), key=lambda h: -h[2])
    rep.hypotheses = ranked
    if ranked:
        s = ranked[0][2]
        rep.confidence = "alta" if s >= 30 else "média" if s >= 18 else "baixa"
    return rep

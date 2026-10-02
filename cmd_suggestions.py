"""Comandos de PowerShell sugeridos conforme o problema. NUNCA são executados automaticamente:
o usuário lê, copia ou escolhe executar um a um (aba 'Comandos sugeridos')."""
from dataclasses import dataclass, field

# kind: "leitura" (só consulta), "altera" (muda algo no sistema ou inicia reparo/verificação)
@dataclass
class Cmd:
    title: str
    desc: str
    cmd: str
    kind: str = "leitura"
    admin: bool = False
    tags: set = field(default_factory=set)


def C(title, desc, cmd, tags, kind="leitura", admin=False):
    return Cmd(title, desc, cmd, kind, admin, set(tags.split()))


COMMANDS = [
    # ---------------------------------------------------------------- geral
    C("Resumo do sistema", "Versão do Windows, build, BIOS e memória total.",
      "Get-ComputerInfo | Select-Object OsName,OsVersion,OsBuildNumber,BiosSMBIOSBIOSVersion,CsTotalPhysicalMemory | Format-List",
      "os crash perf update"),
    C("Quando o PC foi ligado pela última vez", "Mostra o último boot e há quanto tempo o PC está ligado.",
      "$b=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; $b; 'Ligado há: ' + ((Get-Date)-$b).ToString('d\\.hh\\:mm\\:ss')",
      "crash perf power"),
    # ---------------------------------------------------------------- quedas / tela azul
    C("Reinícios inesperados (Kernel-Power 41)", "Lista as últimas 20 vezes em que o Windows reiniciou sem desligar direito.",
      "Get-WinEvent -FilterHashtable @{LogName='System';Id=41} -MaxEvents 20 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id | Format-Table -AutoSize",
      "crash power hw"),
    C("Telas azuis registradas", "Mostra data e código das últimas telas azuis (BugCheck).",
      "Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WER-SystemErrorReporting'} -MaxEvents 20 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Message | Format-List",
      "crash mem driver"),
    C("Erros de hardware (WHEA)", "Erros reportados pela CPU, memória ou barramento PCIe. Qualquer resultado aqui merece atenção.",
      "Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger'} -MaxEvents 20 -ErrorAction SilentlyContinue | Format-List TimeCreated,Id,Message",
      "crash hw mem"),
    C("Arquivos de minidump", "Lista os dumps de memória gerados nas telas azuis (abra no BlueScreenView ou WinDbg).",
      "Get-ChildItem C:\\Windows\\Minidump -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize",
      "crash driver"),
    C("Drivers instalados mais recentemente", "Um driver novo pode ser o culpado se os problemas começaram depois dele.",
      "Get-CimInstance Win32_PnPSignedDriver | Where-Object DriverDate | Sort-Object DriverDate -Descending | Select-Object -First 20 DeviceName,DriverVersion,DriverDate,Manufacturer | Format-Table -AutoSize",
      "crash driver video apps"),
    C("Ver a tela azul antes de reiniciar", "Desliga o reinício automático após falha, para você conseguir ler o código da tela azul.",
      "Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl' -Name AutoReboot -Value 0",
      "crash", kind="altera", admin=True),
    C("Windows em canal de testes (Insider)?", "Mostra se o PC está em um canal Insider, que pode ter bugs.",
      "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\WindowsSelfHost\\Applicability' -ErrorAction SilentlyContinue | Select-Object BranchName,ContentType,Ring | Format-List",
      "os crash"),
    # ---------------------------------------------------------------- memória
    C("Pentes de memória e velocidade", "Mostra fabricante, capacidade e a velocidade anunciada x configurada (EXPO/DOCP).",
      "Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel,Manufacturer,PartNumber,@{n='GB';e={$_.Capacity/1GB}},Speed,ConfiguredClockSpeed | Format-Table -AutoSize",
      "mem crash perf"),
    C("Resultado do Teste de Memória do Windows", "Mostra o resultado dos testes já realizados com o mdsched.",
      "Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-MemoryDiagnostics-Results'} -MaxEvents 10 -ErrorAction SilentlyContinue | Format-List TimeCreated,Message",
      "mem crash"),
    C("Agendar o Teste de Memória do Windows", "Abre o assistente; se você escolher reiniciar, o PC reinicia para testar a RAM.",
      "mdsched.exe", "mem crash", kind="altera"),
    C("Maiores consumidores de memória", "Os 10 programas que mais usam RAM agora.",
      "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name,Id,@{n='RAM(MB)';e={[int]($_.WorkingSet64/1MB)}} | Format-Table -AutoSize",
      "mem perf apps"),
    # ---------------------------------------------------------------- programas
    C("Programas que travaram ou fecharam (evento 1000)", "Mostra o programa, o módulo e o código de erro das últimas falhas.",
      "Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000} -MaxEvents 20 -ErrorAction SilentlyContinue | Select-Object TimeCreated,@{n='Programa';e={$_.Properties[0].Value}},@{n='Modulo';e={$_.Properties[3].Value}},@{n='Codigo';e={$_.Properties[6].Value}} | Format-Table -AutoSize",
      "apps crash mem"),
    C("Programas sem resposta (evento 1002)", "Programas que o Windows encerrou por ficarem travados.",
      "Get-WinEvent -FilterHashtable @{LogName='Application';Id=1002} -MaxEvents 20 -ErrorAction SilentlyContinue | Select-Object TimeCreated,@{n='Programa';e={$_.Properties[0].Value}} | Format-Table -AutoSize",
      "apps perf"),
    # ---------------------------------------------------------------- desempenho / calor
    C("Maiores consumidores de CPU", "Os 10 processos com mais tempo de CPU.",
      "Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Name,Id,@{n='CPU(s)';e={[int]$_.CPU}},@{n='RAM(MB)';e={[int]($_.WorkingSet64/1MB)}} | Format-Table -AutoSize",
      "perf thermal"),
    C("Uso de CPU agora", "Porcentagem de uso do processador neste instante.",
      "(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter \"Name='_Total'\").PercentProcessorTime",
      "perf thermal"),
    C("Plano de energia ativo", "Plano 'Economia' pode deixar o PC lento; 'Equilibrado' ou 'Alto desempenho' costumam ser melhores.",
      "powercfg /getactivescheme; powercfg /list", "perf power thermal"),
    C("Programas que iniciam com o Windows", "Quanto mais itens, mais demorado o boot e mais RAM ocupada.",
      "Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location | Format-Table -AutoSize -Wrap",
      "startup perf"),
    C("Limpar arquivos temporários do usuário", "Apaga o conteúdo da pasta TEMP do seu usuário (arquivos em uso são ignorados).",
      "Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue",
      "disk perf", kind="altera"),
    # ---------------------------------------------------------------- disco
    C("Saúde dos discos", "Estado de saúde informado pelo Windows para cada disco físico.",
      "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,@{n='GB';e={[int]($_.Size/1GB)}} | Format-Table -AutoSize",
      "disk crash perf"),
    C("Desgaste, temperatura e erros do disco (SMART)", "Horas ligado, desgaste do SSD, temperatura e erros de leitura/escrita.",
      "Get-PhysicalDisk | Get-StorageReliabilityCounter | Select-Object DeviceId,Temperature,Wear,ReadErrorsTotal,WriteErrorsTotal,PowerOnHours | Format-Table -AutoSize",
      "disk crash", admin=True),
    C("Espaço livre nos volumes", "Menos de 15% livre deixa o Windows lento.",
      "Get-Volume | Where-Object DriveLetter | Select-Object DriveLetter,FileSystemLabel,@{n='Livre(GB)';e={[int]($_.SizeRemaining/1GB)}},@{n='Total(GB)';e={[int]($_.Size/1GB)}},HealthStatus | Format-Table -AutoSize",
      "disk perf update"),
    C("Erros de disco no registro de eventos", "Eventos 'disk' e 'Ntfs' recentes (setor ruim, falha de leitura, reset).",
      "Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='disk','Ntfs'} -MaxEvents 20 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,ProviderName,Message | Format-List",
      "disk crash"),
    C("Verificar o disco C: (somente leitura)", "CHKDSK online, sem corrigir nada. Pode levar vários minutos.",
      "chkdsk C: /scan", "disk crash", admin=True),
    # ---------------------------------------------------------------- rede
    C("Adaptadores de rede", "Estado e velocidade de cada placa de rede (Wi-Fi/cabo).",
      "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,LinkSpeed | Format-Table -AutoSize",
      "net"),
    C("Configuração de IP, gateway e DNS", "Mostra o IP, o roteador (gateway) e os servidores DNS em uso.",
      "Get-NetIPConfiguration", "net"),
    C("Teste: roteador responde?", "Se o roteador responde mas a internet não, o problema está no provedor.",
      "Test-Connection (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1 -ExpandProperty NextHop) -Count 4",
      "net"),
    C("Teste: internet por IP", "Ping em 8.8.8.8 (sem depender de DNS).", "Test-Connection 8.8.8.8 -Count 4", "net"),
    C("Teste: DNS", "Se falhar enquanto o ping por IP funciona, o problema é DNS.", "Resolve-DnsName google.com", "net"),
    C("Teste: conexão HTTPS", "Testa a porta 443 de um site conhecido.",
      "Test-NetConnection google.com -Port 443 | Select-Object ComputerName,RemotePort,TcpTestSucceeded,PingSucceeded | Format-List",
      "net"),
    C("Limpar o cache de DNS", "Descarta nomes salvos; ajuda quando um site não abre mas outros sim.",
      "Clear-DnsClientCache", "net", kind="altera"),
    # ---------------------------------------------------------------- vídeo
    C("Placa de vídeo e driver", "Modelo, versão e data do driver, resolução e taxa de atualização.",
      "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate | Format-List",
      "gpu video crash"),
    C("Painel da placa NVIDIA (nvidia-smi)", "Uso, temperatura, clocks e consumo da GPU (só com driver NVIDIA).",
      "nvidia-smi", "gpu video thermal"),
    C("Falhas do driver de vídeo (TDR)", "Eventos em que o driver de vídeo parou de responder e foi reiniciado.",
      "Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='nvlddmkm','amdkmdag','Display'} -MaxEvents 20 -ErrorAction SilentlyContinue | Format-List TimeCreated,ProviderName,Message",
      "gpu video crash"),
    # ---------------------------------------------------------------- update / integridade
    C("Atualizações instaladas recentemente", "Se o problema começou depois de uma atualização, ela é suspeita.",
      "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID,Description,InstalledOn | Format-Table -AutoSize",
      "update os crash"),
    C("Serviços do Windows Update", "Os serviços devem existir e não estar desativados.",
      "Get-Service wuauserv,bits,cryptsvc,TrustedInstaller | Select-Object Name,Status,StartType | Format-Table -AutoSize",
      "update"),
    C("Falhas recentes do Windows Update", "Erros registrados pelo cliente de atualização.",
      "Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WindowsUpdateClient';Level=2} -MaxEvents 15 -ErrorAction SilentlyContinue | Format-List TimeCreated,Id,Message",
      "update"),
    C("Verificar integridade (DISM CheckHealth)", "Checagem rápida: só informa se o repositório do Windows está corrompido.",
      "DISM /Online /Cleanup-Image /CheckHealth", "update os disk", admin=True),
    C("Reparar a imagem do Windows (DISM RestoreHealth)", "Baixa e corrige arquivos do sistema. Pode levar de 10 a 30 minutos.",
      "DISM /Online /Cleanup-Image /RestoreHealth", "update os disk", kind="altera", admin=True),
    C("Reparar arquivos do sistema (SFC)", "Verifica e corrige arquivos protegidos do Windows. Pode levar de 5 a 30 minutos.",
      "sfc /scannow", "update os crash apps", kind="altera", admin=True),
    C("Reiniciar o serviço do Windows Update", "Ajuda quando a atualização trava em 'verificando'.",
      "Restart-Service wuauserv", "update", kind="altera", admin=True),
    # ---------------------------------------------------------------- segurança
    C("Estado do antivírus do Windows", "Proteção em tempo real e data das definições de vírus.",
      "Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AntispywareEnabled,AntivirusSignatureLastUpdated | Format-List",
      "security perf"),
    C("Ameaças detectadas pelo Defender", "Histórico de detecções.",
      "Get-MpThreatDetection | Select-Object InitialDetectionTime,ThreatID,Resources | Format-List",
      "security perf"),
    C("Atualizar definições de vírus", "Baixa as definições mais recentes do Defender.",
      "Update-MpSignature", "security", kind="altera"),
    C("Verificação rápida de vírus", "Inicia uma verificação rápida do Defender (usa CPU e disco enquanto roda).",
      "Start-MpScan -ScanType QuickScan", "security perf", kind="altera"),
]


def suggest(scenario_tags, hyp_tags=None, show_all=False):
    """Lista (Cmd, grupo). scenario_tags=None → todos. hyp_tags: tags das hipóteses mais prováveis."""
    out = []
    for c in COMMANDS:
        if show_all:
            grp = "Todos os comandos"
        elif hyp_tags and c.tags & hyp_tags:
            grp = "Ligados às causas prováveis"
        elif scenario_tags is None or c.tags & scenario_tags:
            grp = "Do foco escolhido"
        else:
            continue
        out.append((c, grp))
    order = {"Ligados às causas prováveis": 0, "Do foco escolhido": 1, "Todos os comandos": 2}
    out.sort(key=lambda x: order[x[1]])  # estável: mantém a ordem original dentro do grupo
    return out


AVISO = ("Nenhum comando é executado automaticamente. Você lê, pode copiar para o seu próprio PowerShell ou "
         "escolher executar aqui, um por vez, com confirmação.")


def to_text(items):
    L = ["COMANDOS SUGERIDOS DO POWERSHELL (não são executados automaticamente; você escolhe se e quando rodar)"]
    for c, _g in items:
        flags = ("só consulta" if c.kind == "leitura" else "ALTERA o sistema") + (", exige administrador" if c.admin else "")
        L += [f"  # {c.title} [{flags}]", f"  {c.cmd}", ""]
    return "\n".join(L)

"""Iniciar com o Windows. Como administrador usa o Agendador de Tarefas com privilégios elevados (necessário
para ler as temperaturas); sem administrador usa a chave Run do usuário."""
import os
import subprocess
import sys

TASK_NAME = "PCMonitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def _pythonw():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return pyw if os.path.exists(pyw) else exe


def _run(args):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, creationflags=flags)


def set_enabled(enabled, admin):
    """Retorna (ok, mensagem)."""
    if sys.platform != "win32":
        return False, "Disponível apenas no Windows."
    import winreg
    cmd = f'"{_pythonw()}" "{MAIN}"'
    if enabled:
        if admin:
            r = _run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", cmd,
                      "/sc", "onlogon", "/rl", "highest", "/f"])
            if r.returncode == 0:
                _remove_run_key(winreg)
                return True, "Ativado (tarefa agendada com privilégios de administrador)."
            return False, "Falha ao criar a tarefa: " + (r.stderr or r.stdout).strip()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, TASK_NAME, 0, winreg.REG_SZ, cmd)
            return True, "Ativado, mas sem administrador as temperaturas da CPU podem não aparecer. " \
                         "Abra o programa como administrador e ative de novo para corrigir."
        except OSError as e:
            return False, f"Falha ao gravar no registro: {e}"
    _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    _remove_run_key(winreg)
    return True, "Desativado."


def _remove_run_key(winreg):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, TASK_NAME)
    except OSError:
        pass

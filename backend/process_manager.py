import subprocess
import threading
import uuid
import json
import os
import re
from datetime import datetime
from pathlib import Path

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\[\?[0-9;]*[a-zA-Z]')

SERVICES_FILE = Path(__file__).parent / "services.json"
MAX_LOG_LINES = 5000


def _extract_port(script_path: str) -> str:
    try:
        for enc in ('utf-8', 'gbk', 'latin-1'):
            try:
                text = Path(script_path).read_text(encoding=enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            return ""
        m = re.search(r'(?:localhost|127\.0\.0\.1|0\.0\.0\.0)[:/](\d{2,5})', text)
        if m:
            return m.group(1)
        m = re.search(r'[Pp][Oo][Rr][Tt][=\s:]+?(\d{2,5})', text)
        if m:
            return m.group(1)
        m = re.search(r'http\.server\s+(\d{2,5})', text)
        if m:
            return m.group(1)
    except:
        pass
    return ""


class ManagedProcess:
    def __init__(self, id: str, name: str, script_path: str, cwd: str, command: str, port: str = ""):
        self.id = id
        self.name = name
        self.script_path = script_path
        self.cwd = cwd
        self.command = command
        self.port = port
        self.process: subprocess.Popen = None
        self.log_buffer: list[str] = []
        self.subscribers: list = []
        self.status = "stopped"
        self.pid = None
        self.started_at = None
        self.exit_code = None
        self.restart_count = 0

    def start(self):
        if self.process and self.process.poll() is None:
            return False
        
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=False,
                bufsize=0,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self.status = "running"
            self.pid = self.process.pid
            self.started_at = datetime.now().isoformat()
            self.exit_code = None
            threading.Thread(target=self._read_output, daemon=True).start()
            return True
        except Exception as e:
            self.status = "error"
            self.log_buffer.append(f"[cmd-patrol] Failed to start: {e}\n")
            return False

    def _read_output(self):
        try:
            buf = b''
            while True:
                chunk = self.process.stdout.read(1)
                if not chunk:
                    if buf:
                        self._emit_line(buf)
                    break
                buf += chunk
                if chunk == b'\n':
                    self._emit_line(buf)
                    buf = b''
        except:
            pass
        finally:
            self.exit_code = self.process.poll()
            self.status = "stopped"
            self.pid = None

    def _emit_line(self, raw: bytes):
        for enc in ('utf-8', 'gbk', 'cp936', 'latin-1'):
            try:
                line = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            line = raw.decode('latin-1')
        line = ANSI_ESCAPE.sub('', line)
        self.log_buffer.append(line)
        if len(self.log_buffer) > MAX_LOG_LINES:
            self.log_buffer.pop(0)
        for callback in list(self.subscribers):
            try:
                callback(self.id, line)
            except:
                pass

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.status = "stopped"
            self.pid = None
            return True
        return False

    def restart(self):
        self.stop()
        self.restart_count += 1
        return self.start()

    def to_dict(self):
        if self.process is not None:
            rc = self.process.poll()
            if rc is None:
                self.status = "running"
            else:
                self.status = "stopped"
                self.exit_code = rc
                self.pid = None
        return {
            "id": self.id,
            "name": self.name,
            "script_path": self.script_path,
            "cwd": self.cwd,
            "command": self.command,
            "port": self.port,
            "status": self.status,
            "pid": self.pid,
            "started_at": self.started_at,
            "exit_code": self.exit_code,
            "restart_count": self.restart_count,
        }

    def to_persist(self):
        return {
            "id": self.id,
            "name": self.name,
            "script_path": self.script_path,
            "cwd": self.cwd,
            "command": self.command,
            "port": self.port,
        }


class ProcessManager:
    def __init__(self):
        self.processes: dict[str, ManagedProcess] = {}
        self._load()

    def _load(self):
        if SERVICES_FILE.exists():
            try:
                data = json.loads(SERVICES_FILE.read_text(encoding="utf-8"))
                for item in data:
                    proc = ManagedProcess(
                        id=item["id"],
                        name=item["name"],
                        script_path=item["script_path"],
                        cwd=item["cwd"],
                        command=item["command"],
                        port=item.get("port", ""),
                    )
                    if not proc.port:
                        proc.port = _extract_port(proc.script_path)
                    self.processes[proc.id] = proc
            except:
                pass

    def _save(self):
        data = [p.to_persist() for p in self.processes.values()]
        SERVICES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def register(self, script_path: str, name: str = None) -> ManagedProcess:
        script_path = os.path.abspath(script_path)
        cwd = os.path.dirname(script_path)
        
        if name is None:
            name = Path(script_path).stem
            if name.startswith("start_"):
                name = name[6:]
        
        ext = Path(script_path).suffix.lower()
        if ext in (".cmd", ".bat"):
            command = f'cmd.exe /c "{script_path}"'
        elif ext == ".ps1":
            command = f'powershell -ExecutionPolicy Bypass -File "{script_path}"'
        elif ext == ".sh":
            command = f'bash "{script_path}"'
        else:
            command = f'"{script_path}"'

        proc = ManagedProcess(
            id=str(uuid.uuid4()),
            name=name,
            script_path=script_path,
            cwd=cwd,
            command=command,
            port=_extract_port(script_path),
        )
        self.processes[proc.id] = proc
        self._save()
        return proc

    def unregister(self, id: str) -> bool:
        if id in self.processes:
            self.processes[id].stop()
            del self.processes[id]
            self._save()
            return True
        return False

    def get(self, id: str) -> ManagedProcess:
        return self.processes.get(id)

    def list_all(self) -> list[ManagedProcess]:
        return list(self.processes.values())

    def start(self, id: str) -> bool:
        proc = self.get(id)
        return proc.start() if proc else False

    def stop(self, id: str) -> bool:
        proc = self.get(id)
        return proc.stop() if proc else False

    def restart(self, id: str) -> bool:
        proc = self.get(id)
        return proc.restart() if proc else False

    def subscribe_logs(self, id: str, callback):
        proc = self.get(id)
        if proc:
            proc.subscribers.append(callback)

    def unsubscribe_logs(self, id: str, callback):
        proc = self.get(id)
        if proc and callback in proc.subscribers:
            proc.subscribers.remove(callback)

    def get_logs(self, id: str) -> list[str]:
        proc = self.get(id)
        return proc.log_buffer if proc else []

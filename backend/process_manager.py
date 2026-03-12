import subprocess
import threading
import time
import uuid
import json
import os
import re
import ctypes
import ctypes.wintypes
from datetime import datetime
from pathlib import Path

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\[\?[0-9;]*[a-zA-Z]')

SERVICES_FILE = Path(__file__).parent / "services.json"
MAX_LOG_LINES = 5000
LOG_MAX_AGE = 3600  # seconds, prune logs older than 1 hour

_kernel32 = ctypes.windll.kernel32
# Ensure 64-bit HANDLE return types on x64 Windows
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.CreateJobObjectW.restype = ctypes.wintypes.HANDLE
_kernel32.AssignProcessToJobObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.HANDLE]
_kernel32.AssignProcessToJobObject.restype = ctypes.wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
_kernel32.TerminateJobObject.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.SetInformationJobObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.wintypes.DWORD]


def _pid_alive(pid) -> bool:
    """Check if a PID is still alive at the OS level (Windows)."""
    if pid is None:
        return False
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        _kernel32.CloseHandle(handle)
        return exit_code.value == STILL_ACTIVE
    except Exception:
        return False


# ── Job Object helpers (Windows) ──────────────────────────────
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64),
        ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', ctypes.c_uint32),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', ctypes.c_uint32),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', ctypes.c_uint32),
        ('SchedulingClass', ctypes.c_uint32),
    ]

class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ('IoInfo', _IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]

def _create_job_for_process(proc_handle):
    """Create a Job Object with KILL_ON_JOB_CLOSE and assign the process to it."""
    try:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _kernel32.SetInformationJobObject(
            job, 9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info), ctypes.sizeof(info))
        _kernel32.AssignProcessToJobObject(job, int(proc_handle))
        return job
    except Exception:
        return None


def _kill_pid_tree(pid):
    """Kill a PID and all its descendants using taskkill /T."""
    if pid is None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/F", "/T"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def _kill_pids(pids):
    """Kill a list of individual PIDs."""
    for pid in (pids or []):
        try:
            if _pid_alive(pid):
                subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"],
                               capture_output=True, timeout=5)
        except Exception:
            pass


def _find_children_by_parent(parent_pid):
    """Fast child PID lookup using CreateToolhelp32Snapshot (instant, no subprocess)."""
    if parent_pid is None:
        return []
    TH32CS_SNAPPROCESS = 0x2
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_char * 260),
        ]
    children = []
    try:
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return []
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(pe)
        if _kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                if pe.th32ParentProcessID == int(parent_pid):
                    children.append(pe.th32ProcessID)
                if not _kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
        _kernel32.CloseHandle(snap)
    except Exception:
        pass
    return children


def _terminate_pid(pid):
    """Instantly terminate a process by PID via kernel32 (no subprocess)."""
    try:
        PROCESS_TERMINATE = 0x0001
        h = _kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if h:
            _kernel32.TerminateProcess(h, 1)
            _kernel32.CloseHandle(h)
    except Exception:
        pass


def _kill_child_conhosts(pid):
    """Kill conhost.exe child processes of the given PID (instant, pure ctypes)."""
    for child_pid in _find_children_by_parent(pid):
        _terminate_pid(child_pid)


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
    def __init__(self, id: str, name: str, script_path: str, cwd: str, command: str, port: str = "", config_file: str = "", pinned: bool = False, alias: str = "", group: str = ""):
        self.id = id
        self.name = name
        self.alias = alias
        self.group = group
        self.script_path = script_path
        self.cwd = cwd
        self.command = command
        self.port = port
        self.config_file = config_file
        self.pinned = pinned
        self.process: subprocess.Popen = None
        self.job_handle = None
        self.child_pids: list = []  # snapshot of descendant PIDs for orphan cleanup
        self.log_buffer: list = []  # list of (float_timestamp, str_line)
        self.log_pruned_count: int = 0  # total lines pruned, for offset tracking
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
                shell=False,
                text=False,
                bufsize=0,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            )
            # Assign to Job Object so all descendants are tracked and killable
            self.job_handle = _create_job_for_process(self.process._handle)
            self.status = "running"
            self.pid = self.process.pid
            self.child_pids = []
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
            fd = self.process.stdout.fileno()
            buf = b''
            while True:
                try:
                    chunk = os.read(fd, 8192)
                except OSError:
                    break
                if not chunk:
                    if buf:
                        self._emit_line(buf)
                    break
                buf += chunk
                while True:
                    idx_n = buf.find(b'\n')
                    idx_r = buf.find(b'\r')
                    if idx_n == -1 and idx_r == -1:
                        break
                    if idx_n == -1:
                        idx = idx_r
                    elif idx_r == -1:
                        idx = idx_n
                    else:
                        idx = min(idx_n, idx_r)
                    line = buf[:idx]
                    if idx == idx_r and idx + 1 < len(buf) and buf[idx + 1:idx + 2] == b'\n':
                        buf = buf[idx + 2:]
                    else:
                        buf = buf[idx + 1:]
                    if line:
                        self._emit_line(line)
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
        now = time.time()
        self.log_buffer.append((now, line))
        if len(self.log_buffer) > MAX_LOG_LINES:
            self.log_buffer.pop(0)
            self.log_pruned_count += 1
        if len(self.log_buffer) % 200 == 0:
            self._prune_logs(now)
        for callback in list(self.subscribers):
            try:
                callback(self.id, line)
            except:
                pass

    def _prune_logs(self, now=None):
        if now is None:
            now = time.time()
        cutoff = now - LOG_MAX_AGE
        count = 0
        while self.log_buffer and self.log_buffer[0][0] < cutoff:
            self.log_buffer.pop(0)
            count += 1
        self.log_pruned_count += count

    def _collect_child_pids(self):
        """Snapshot all descendant PIDs using fast ctypes API."""
        if not self.pid:
            return
        self.child_pids = [p for p in _find_children_by_parent(self.pid) if p != self.pid]

    def _terminate_job(self):
        """Terminate the Job Object, killing all processes in it."""
        if self.job_handle:
            try:
                _kernel32.TerminateJobObject(self.job_handle, 1)
                _kernel32.CloseHandle(self.job_handle)
            except Exception:
                pass
            self.job_handle = None

    def _force_cleanup(self):
        """Force-clean a ghost process: kill job/tree, close pipe, reset state."""
        saved_pid = self.pid
        self._terminate_job()
        _kill_pid_tree(self.pid)
        _kill_pids(self.child_pids)
        _kill_child_conhosts(saved_pid)
        try:
            if self.process and self.process.stdout:
                self.process.stdout.close()
        except Exception:
            pass
        self.exit_code = self.process.poll() if self.process else None
        self.status = "stopped"
        self.pid = None
        self.child_pids = []

    def stop(self):
        if self.status == "orphan" and self.pid:
            saved_pid = self.pid
            _kill_pid_tree(self.pid)
            _kill_pids(self.child_pids)
            _kill_child_conhosts(saved_pid)
            self.status = "stopped"
            self.pid = None
            self.child_pids = []
            self.process = None
            return True
        if self.process and self.process.poll() is None:
            if not _pid_alive(self.pid):
                self._force_cleanup()
                return True
            saved_pid = self.pid
            # Terminate via Job Object (kills entire tree atomically)
            self._terminate_job()
            # Fallback: also taskkill tree in case job didn't cover everything
            _kill_pid_tree(self.pid)
            _kill_child_conhosts(saved_pid)
            try:
                self.process.wait(timeout=5)
            except Exception:
                pass
            try:
                if self.process.stdout:
                    self.process.stdout.close()
            except Exception:
                pass
            self.status = "stopped"
            self.pid = None
            self.child_pids = []
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
                if self.pid and not _pid_alive(self.pid):
                    self._force_cleanup()
                else:
                    self.status = "running"
            else:
                self.status = "stopped"
                self.exit_code = rc
                self.pid = None
        return {
            "id": self.id,
            "name": self.name,
            "alias": self.alias,
            "group": self.group,
            "script_path": self.script_path,
            "cwd": self.cwd,
            "command": self.command,
            "port": self.port,
            "config_file": self.config_file,
            "pinned": self.pinned,
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
            "alias": self.alias,
            "group": self.group,
            "script_path": self.script_path,
            "cwd": self.cwd,
            "command": self.command,
            "port": self.port,
            "config_file": self.config_file,
            "pinned": self.pinned,
            "last_pid": self.pid,
            "last_status": self.status,
            "child_pids": self.child_pids,
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
                        config_file=item.get("config_file", ""),
                        pinned=item.get("pinned", False),
                        alias=item.get("alias", ""),
                        group=item.get("group", ""),
                    )
                    if not proc.port:
                        proc.port = _extract_port(proc.script_path)
                    last_pid = item.get("last_pid")
                    last_status = item.get("last_status", "stopped")
                    saved_child_pids = item.get("child_pids", [])
                    if last_status == "running" and last_pid:
                        if _pid_alive(last_pid):
                            proc.status = "orphan"
                            proc.pid = last_pid
                            proc.child_pids = saved_child_pids
                        else:
                            # Parent dead, but children may still be alive
                            _kill_pids(saved_child_pids)
                            proc.status = "stopped"
                            proc.pid = None
                            proc.child_pids = []
                    self.processes[proc.id] = proc
            except:
                pass
        self._save()

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

    def health_check(self):
        """Sweep all services: snapshot child PIDs and clean up ghost processes."""
        dirty = False
        for proc in self.processes.values():
            if proc.status == "running" and proc.pid:
                if not _pid_alive(proc.pid):
                    proc._force_cleanup()
                    dirty = True
                else:
                    # Periodically snapshot child PIDs for orphan recovery
                    proc._collect_child_pids()
                    dirty = True
        if dirty:
            self._save()

    def cleanup_and_start_all(self):
        """Clean up all orphan/zombie processes, then start all services."""
        for proc in self.processes.values():
            if proc.status == "orphan" and proc.pid:
                proc.stop()
            elif proc.status == "running" and proc.pid:
                if not _pid_alive(proc.pid):
                    proc._force_cleanup()
        self._save()
        started = 0
        for proc in self.processes.values():
            if proc.status != "running":
                if proc.start():
                    started += 1
        self._save()
        return started

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

    def get_logs(self, id: str, offset: int = 0):
        proc = self.get(id)
        if not proc:
            return [], 0, 0
        proc._prune_logs()
        buf = proc.log_buffer
        total = proc.log_pruned_count + len(buf)
        idx = max(0, offset - proc.log_pruned_count)
        lines = [entry[1] for entry in buf[idx:]]
        return lines, total, proc.log_pruned_count

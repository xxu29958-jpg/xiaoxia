"""Physical Windows process ownership and concurrent launcher verification."""

from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


SCRIPT = Path(__file__).resolve().parents[1] / "runtime" / "service-start.ps1"
FLAGS = subprocess.CREATE_NO_WINDOW


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def driver(folder, entry, port, *, name, executable=None):
    path = folder / (name + ".ps1")
    arguments = f'"{entry}" {port}'
    path.write_text(
        "$ErrorActionPreference='Stop'\n. " + quote(SCRIPT) + "\n"
        f"Start-OwnedService -Name {quote(name)} -Executable {quote(executable or sys._base_executable)} "
        f"-Arguments {quote(arguments)} -WorkingDirectory {quote(folder)} "
        f"-Port {port} -EntryMarker {quote(entry)} -PidFile {quote(folder / (name + '.pid'))} "
        f"-LogPrefix {quote(folder / name)} -StartupSeconds 5\n",
        encoding="utf-8-sig",
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(path)]


class LaunchAttempt:
    def __init__(self, command):
        # Descendants can inherit pipe handles on Windows. Capture to files so
        # service lifetime cannot keep a finished launcher's output pipe open.
        self.stdout = tempfile.TemporaryFile()
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=self.stdout,
            stderr=self.stderr,
            creationflags=FLAGS,
        )

    @property
    def returncode(self):
        return self.process.returncode

    def communicate(self, timeout):
        try:
            self.process.wait(timeout=timeout)
            self.stdout.seek(0)
            self.stderr.seek(0)
            return tuple(
                f.read().decode("utf-8", errors="replace")
                for f in (self.stdout, self.stderr)
            )
        finally:
            self.stdout.close()
            self.stderr.close()


def start(command):
    return LaunchAttempt(command)


def stop_recorded(entry):
    records = entry.with_suffix(".started")
    if not records.exists():
        return
    for pid in records.read_text().splitlines():
        pid = int(pid)
        # Validate the absolute test entry before stopping a process. Never use
        # a port or a stale PID as authority to stop an unrelated program.
        code = f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; if ($p -and $p.CommandLine.Contains({quote(entry)})) {{ Stop-Process -Id {pid} -ErrorAction Stop }}"
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", code],
            capture_output=True,
            creationflags=FLAGS,
            timeout=15,
            check=True,
        )


def server(entry):
    entry.write_text(
        "import os,socket,sys,time\nfrom pathlib import Path\n"
        "with Path(__file__).with_suffix('.started').open('a') as f:f.write(str(os.getpid())+'\\n')\n"
        "s=socket.socket();s.bind(('127.0.0.1',int(sys.argv[1])));s.listen()\n"
        "while True:\n c,a=s.accept();c.sendall(b'owned');c.close()\n",
        encoding="utf-8",
    )


def test_concurrent_launch_reuses_one_owned_process(tmp_path):
    entry = tmp_path / "service.py"
    server(entry)
    port = free_port()
    command = driver(tmp_path, entry, port, name="concurrent-" + str(port))
    attempts = [start(command), start(command)]
    try:
        for attempt in attempts:
            out, err = attempt.communicate(timeout=45)
            assert attempt.returncode == 0, out + err
        assert len(entry.with_suffix(".started").read_text().splitlines()) == 1
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            assert client.recv(5) == b"owned"
    finally:
        stop_recorded(entry)


def test_foreign_listener_is_rejected_and_left_running(tmp_path):
    foreign = tmp_path / "foreign.py"
    server(foreign)
    port = free_port()
    process = subprocess.Popen(
        [sys._base_executable, str(foreign), str(port)], creationflags=FLAGS
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        expected = tmp_path / "expected.py"
        server(expected)
        attempt = start(driver(tmp_path, expected, port, name="foreign-" + str(port)))
        out, err = attempt.communicate(timeout=30)
        assert attempt.returncode != 0
        assert "different process" in out + err
        assert process.poll() is None
        assert not expected.with_suffix(".started").exists()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_early_exit_is_failure_and_not_a_ready_service(tmp_path):
    entry = tmp_path / "failed.py"
    entry.write_text("raise SystemExit(3)\n", encoding="utf-8")
    port = free_port()
    attempt = start(driver(tmp_path, entry, port, name="failed-" + str(port)))
    out, err = attempt.communicate(timeout=30)
    assert attempt.returncode != 0
    assert "exited during startup" in out + err
    assert "is listening" not in out


def test_shutdown_runs_cleanup_and_refuses_a_different_entry(tmp_path):
    entry = tmp_path / "service.py"
    entry.write_text(
        "import asyncio,os,sys\nfrom pathlib import Path\n"
        "p=Path(__file__)\np.with_suffix('.started').write_text(str(os.getpid()))\n"
        "async def run():\n"
        " server=await asyncio.start_server(lambda r,w:w.close(),'127.0.0.1',int(sys.argv[1]))\n"
        " try:\n  while True:await asyncio.sleep(.1)\n"
        " finally:\n  server.close();await server.wait_closed();p.with_suffix('.closed').write_text('clean')\n"
        "asyncio.run(run())\n",
        encoding="utf-8",
    )
    port = free_port()
    launch = start(driver(tmp_path, entry, port, name="shutdown-" + str(port), executable=sys.executable))
    try:
        out, err = launch.communicate(timeout=30)
        assert launch.returncode == 0, out + err
        pid = int(entry.with_suffix(".started").read_text())
        stop_script = SCRIPT.with_name("graceful_stop.py")
        command = [sys.executable, str(stop_script), str(pid), sys.executable]
        rejected = subprocess.run(command + [str(tmp_path / "other.py")],
            capture_output=True, creationflags=FLAGS, timeout=15)
        assert rejected.returncode != 0
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        stopped = subprocess.run(command + [str(entry), "--timeout", "10"],
            capture_output=True, creationflags=FLAGS, timeout=20)
        assert stopped.returncode == 0, stopped.stderr.decode(errors="replace")
        assert entry.with_suffix(".closed").read_text() == "clean"
        with socket.socket() as connection:
            assert connection.connect_ex(("127.0.0.1", port)) != 0
    finally:
        stop_recorded(entry)

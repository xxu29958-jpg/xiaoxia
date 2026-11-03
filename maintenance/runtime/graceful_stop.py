"""Stop an owned Python service through its normal Ctrl+C cleanup on Windows."""

import argparse
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

import psutil


def is_owned(process, executable, entry):
    """Validate a service entrypoint through its workers or virtualenv stub.

    Args:
        process: Live candidate process.
        executable: Resolved interpreter or launcher path.
        entry: Resolved service entrypoint path.

    Returns:
        Whether the process belongs to the exact service invocation.
    """
    for _ in range(4):
        try:
            if Path(process.exe()).resolve() == executable:
                arguments = process.cmdline()[1:]
                entry_arguments = (
                    arguments[1:] if arguments[:1] == ["-u"] else arguments
                )
                if (
                    entry_arguments
                    and (Path(process.cwd()) / entry_arguments[0]).resolve() == entry
                ):
                    return True
                # A venv stub forwards the same arguments to a different binary.
                # An ordinary parent merely having a service child is not owned.
                for child in process.children():
                    if (
                        entry_arguments
                        and Path(child.exe()).resolve() != executable
                        and child.cmdline()[1:] == arguments
                        and (Path(child.cwd()) / entry_arguments[0]).resolve() == entry
                    ):
                        return True
                # Workers can use the same interpreter with their own entry.
                # Continue validating the owner rather than rejecting them here.
            process = process.parent()
            if process is None:
                return False
        except (OSError, psutil.Error):
            return False
    return False


def stop(pid, executable, entry, timeout):
    """Signal only a console whose other members all belong to this service.

    Args:
        pid: Current listener PID, checked independently of the PID file.
        executable: Absolute interpreter or virtualenv launcher path.
        entry: Absolute service entrypoint path.
        timeout: Maximum seconds to wait for normal shutdown.

    Raises:
        RuntimeError: Ownership cannot be proven or cleanup does not finish.
        OSError: Windows cannot attach to or signal the console.
    """
    executable, entry = Path(executable).resolve(), Path(entry).resolve()
    target = psutil.Process(pid)
    if not is_owned(target, executable, entry):
        raise RuntimeError("Process does not belong to the expected service")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(pid):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # CTRL_C_EVENT reaches every process in this console, so verify all
        # participants. A PID/process-group selector cannot narrow this signal.
        buffer = (wintypes.DWORD * 128)()
        count = kernel.GetConsoleProcessList(buffer, len(buffer))
        if not 0 < count <= len(buffer):
            raise RuntimeError("Cannot enumerate the service console")
        members = [
            psutil.Process(value) for value in buffer[:count] if value != os.getpid()
        ]
        if not members or not all(is_owned(p, executable, entry) for p in members):
            raise RuntimeError(
                "Console is shared with a different program; no signal sent"
            )
        if not target.is_running() or not kernel.SetConsoleCtrlHandler(None, True):
            raise RuntimeError("Service changed before shutdown")
        if not kernel.GenerateConsoleCtrlEvent(0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.FreeConsole()
    _, pending = psutil.wait_procs(members, timeout=timeout)
    if pending:
        raise RuntimeError(
            "Service did not finish cleanup; no forced kill was attempted"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    parser.add_argument("executable", type=Path)
    parser.add_argument("entry", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    stop(args.pid, args.executable, args.entry, args.timeout)
    print("Owned service finished shutdown")

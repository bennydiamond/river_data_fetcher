import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ManagedScript:
    name: str
    cmd: List[str]


class ScriptRunner:
    def __init__(
        self, script: ManagedScript, restart_on_failure: bool, restart_delay: int
    ):
        self.script = script
        self.restart_on_failure = restart_on_failure
        self.restart_delay = restart_delay
        self.process: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._io_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            self.script.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._io_thread = threading.Thread(target=self._stream_output, daemon=True)
        self._io_thread.start()
        print(
            f"[manager] started '{self.script.name}' (pid={self.process.pid})",
            flush=True,
        )

    def _stream_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            print(f"[{self.script.name}] {line.rstrip()}", flush=True)

    def poll(self) -> Optional[int]:
        if not self.process:
            return None
        return self.process.poll()

    def stop(self, sig: int = signal.SIGTERM) -> None:
        self._stop_event.set()
        if not self.process:
            return
        if self.process.poll() is not None:
            return
        try:
            self.process.send_signal(sig)
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def handle_exit(self) -> bool:
        """Returns True if restarted, False otherwise."""
        if not self.process:
            return False

        rc = self.process.poll()
        if rc is None:
            return False

        print(f"[manager] '{self.script.name}' exited with code {rc}", flush=True)

        if self._stop_event.is_set():
            return False

        if self.restart_on_failure and rc != 0:
            print(
                f"[manager] restarting '{self.script.name}' in {self.restart_delay}s...",
                flush=True,
            )
            time.sleep(self.restart_delay)
            self.start()
            return True

        return False


class UnifiedRunner:
    def __init__(self) -> None:
        python_exe = sys.executable
        restart_on_failure = os.environ.get("RESTART_ON_FAILURE", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        restart_delay = int(os.environ.get("RESTART_DELAY_SECONDS", "5"))

        self.runners: Dict[str, ScriptRunner] = {
            "river": ScriptRunner(
                ManagedScript(
                    name="river",
                    cmd=[python_exe, "/app/fetcher/river_data_fetcher.py"],
                ),
                restart_on_failure=restart_on_failure,
                restart_delay=restart_delay,
            ),
            "graph": ScriptRunner(
                ManagedScript(
                    name="graph",
                    cmd=[python_exe, "/app/graph/download_graph.py"],
                ),
                restart_on_failure=restart_on_failure,
                restart_delay=restart_delay,
            ),
        }
        self._shutdown = threading.Event()

    def _register_signals(self) -> None:
        def _handler(signum, _frame):
            print(f"[manager] received signal {signum}, shutting down...", flush=True)
            self._shutdown.set()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def run(self) -> int:
        print("[manager] unified runner starting", flush=True)
        self._register_signals()

        for runner in self.runners.values():
            runner.start()

        exit_code = 0
        while not self._shutdown.is_set():
            time.sleep(1)
            for name, runner in self.runners.items():
                rc = runner.poll()
                if rc is None:
                    continue

                restarted = runner.handle_exit()
                if restarted:
                    continue

                # If a process exited cleanly (e.g. script ended), keep manager behavior explicit.
                # In non-restart mode we stop everything to avoid partial service state.
                if not runner.restart_on_failure:
                    print(
                        f"[manager] '{name}' exited and restart is disabled; stopping all.",
                        flush=True,
                    )
                    self._shutdown.set()
                    exit_code = rc if rc is not None else 1
                    break

                # Restart is enabled but process ended with 0; also stop all so container can be restarted if desired.
                if rc == 0:
                    print(
                        f"[manager] '{name}' exited with 0; stopping all managed processes.",
                        flush=True,
                    )
                    self._shutdown.set()
                    break

        for runner in self.runners.values():
            runner.stop()

        print("[manager] shutdown complete", flush=True)
        return exit_code


if __name__ == "__main__":
    sys.exit(UnifiedRunner().run())

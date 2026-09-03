"""
Ponte Python <-> Go Engine
Comunica com o motor Go via stdin/stdout usando JSON
"""
import subprocess
import json
import threading
import os
import time
from queue import Queue, Empty

from config import GO_ENGINE_PATH, GO_ENGINE_TIMEOUT
from utils import logger, Display


class GoEngineError(Exception):
    pass


class GoEngine:
    def __init__(self, engine_path=None):
        self.engine_path = engine_path or GO_ENGINE_PATH
        self.process = None
        self.stdout_queue = Queue()
        self.stderr_thread = None
        self.stdout_thread = None
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        """Inicia o processo Go"""
        if self.process is not None:
            return True

        if not os.path.exists(self.engine_path):
            raise GoEngineError(
                f"Go engine não encontrado em {self.engine_path}. "
                f"Compile primeiro: cd engine-go && go build -o ddk-engine"
            )

        try:
            self.process = subprocess.Popen(
                [self.engine_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._running = True

            self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
            self.stdout_thread.start()

            self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            self.stderr_thread.start()

            # Ping para confirmar inicialização
            resp = self.send_command({"command": "ping"})
            if resp and resp.get("success"):
                Display.info(f"[green]Go Engine ativo (v{resp.get('version', '?')})[/green]")
                return True
            else:
                raise GoEngineError("Engine Go não respondeu ao ping")

        except Exception as e:
            logger.error(f"Falha ao iniciar Go Engine: {e}")
            self.stop()
            raise

    def _read_stdout(self):
        try:
            while self._running and self.process:
                line = self.process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    try:
                        data = json.loads(line)
                        self.stdout_queue.put(data)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Go stdout inválido: {line[:100]} | {e}")
        except Exception as e:
            logger.error(f"Erro leitura stdout Go: {e}")

    def _read_stderr(self):
        try:
            while self._running and self.process:
                line = self.process.stderr.readline()
                if not line:
                    break
                logger.debug(f"[GO-STDERR] {line.strip()}")
        except Exception:
            pass

    def send_command(self, command_dict, timeout=GO_ENGINE_TIMEOUT):
        """Envia comando e aguarda resposta única"""
        with self._lock:
            if not self.process:
                raise GoEngineError("Engine não iniciado")

            try:
                cmd_json = json.dumps(command_dict) + "\n"
                self.process.stdin.write(cmd_json)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise GoEngineError(f"Pipe quebrado: {e}")

            try:
                return self.stdout_queue.get(timeout=timeout)
            except Empty:
                raise GoEngineError(f"Timeout aguardando resposta ({timeout}s)")

    def send_email(self, server, task, proxy=None):
        """
        Envia um único email via Go.
        server: dict {host, port, username, password, use_ssl}
        task: dict {to, from, raw_message}
        proxy: dict opcional {type, host, port, username, password}
        """
        cmd = {
            "command": "send",
            "server": server,
            "task": task,
        }
        if proxy:
            cmd["proxy"] = proxy

        return self.send_command(cmd)

    def send_batch(self, server, tasks, proxy=None, max_conns=1):
        """
        Envia batch reutilizando conexão SMTP.
        Retorna lista de resultados.
        """
        cmd = {
            "command": "batch",
            "server": server,
            "tasks": tasks,
            "max_conns": max_conns,
        }
        if proxy:
            cmd["proxy"] = proxy

        with self._lock:
            if not self.process:
                raise GoEngineError("Engine não iniciado")

            cmd_json = json.dumps(cmd) + "\n"
            self.process.stdin.write(cmd_json)
            self.process.stdin.flush()

            results = []
            deadline = time.time() + GO_ENGINE_TIMEOUT * len(tasks)

            while time.time() < deadline:
                try:
                    resp = self.stdout_queue.get(timeout=GO_ENGINE_TIMEOUT)
                    if resp.get("batch_complete"):
                        break
                    results.append(resp)
                except Empty:
                    logger.warning("Timeout no batch Go")
                    break

            return results

    def stop(self):
        self._running = False
        if self.process:
            try:
                self.send_command({"command": "shutdown"}, timeout=5)
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
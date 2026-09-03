"""
ProxyManager com Rotação Round-Robin + Health Check
"""
import threading
import time
from collections import defaultdict
from utils import load_file_lines, logger
from config import PROXY_FILE, PROXY_TYPE


class ProxyManager:
    def __init__(self, proxy_file=None):
        self.proxy_file = proxy_file or PROXY_FILE
        self.proxies = []
        self.lock = threading.Lock()
        self.failed_proxies = set()
        self.proxy_usage = defaultdict(int)
        self.last_used = {}
        self.current_index = 0
        self._load_proxies()

    def _load_proxies(self):
        lines = load_file_lines(self.proxy_file)
        for line in lines:
            parts = line.strip().split(":")
            if len(parts) == 4:
                proxy = {"host": parts[0], "port": int(parts[1]),
                         "username": parts[2], "password": parts[3],
                         "type": PROXY_TYPE}
                self.proxies.append(proxy)
                self.last_used[proxy["host"]] = 0
            elif len(parts) == 2:
                proxy = {"host": parts[0], "port": int(parts[1]),
                         "username": None, "password": None,
                         "type": PROXY_TYPE}
                self.proxies.append(proxy)
                self.last_used[proxy["host"]] = 0

        # Silenciado do terminal (vai apenas para o arquivo de log para manter a tela limpa)
        logger.info(f"Carregadas {len(self.proxies)} proxies no ProxyManager")

    def get_proxy(self):
        with self.lock:
            if not self.proxies:
                return None
            available = [p for p in self.proxies
                         if f"{p['host']}:{p['port']}" not in self.failed_proxies]
            if not available:
                logger.warning("Todas as proxies falharam, resetando...")
                self.failed_proxies.clear()
                available = self.proxies

            proxy = available[self.current_index % len(available)]
            self.current_index += 1
            self.proxy_usage[proxy["host"]] += 1
            self.last_used[proxy["host"]] = time.time()
            return proxy

    def mark_failed(self, proxy):
        with self.lock:
            key = f"{proxy['host']}:{proxy['port']}"
            self.failed_proxies.add(key)
            logger.warning(f"Proxy falhou: {key}")

    def has_proxies(self):
        return len(self.proxies) > 0

    def get_active_count(self):
        return len([p for p in self.proxies
                    if f"{p['host']}:{p['port']}" not in self.failed_proxies])
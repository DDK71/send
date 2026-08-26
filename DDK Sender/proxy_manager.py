"""
ProxyManager com Rotação Round-Robin Forçada
Cada chamada retorna uma proxy DIFERENTE
"""

import socks
import socket
import random
import threading
import time
from collections import defaultdict
from utils import load_file_lines, logger, Display
from config import PROXY_FILE, PROXY_TYPE, PROXY_TIMEOUT


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
                proxy = {
                    "host": parts[0],
                    "port": int(parts[1]),
                    "username": parts[2],
                    "password": parts[3],
                }
                self.proxies.append(proxy)
                self.last_used[proxy['host']] = 0
            elif len(parts) == 2:
                proxy = {
                    "host": parts[0],
                    "port": int(parts[1]),
                    "username": None,
                    "password": None,
                }
                self.proxies.append(proxy)
                self.last_used[proxy['host']] = 0

        Display.info(f"Carregadas {len(self.proxies)} proxies")
        
        if len(self.proxies) > 1:
            Display.info(f"Rotacao round-robin ativada: cada conexao usa proxy diferente")

    def get_proxy(self):
        """Retorna proxy com rotação round-robin forçada"""
        with self.lock:
            if not self.proxies:
                return None

            available = [
                p for p in self.proxies
                if f"{p['host']}:{p['port']}" not in self.failed_proxies
            ]

            if not available:
                logger.warning("Todas as proxies falharam, resetando...")
                self.failed_proxies.clear()
                available = self.proxies

            # ROTAÇÃO ROUND-ROBIN FORÇADA
            proxy = available[self.current_index % len(available)]
            self.current_index += 1
            
            self.proxy_usage[proxy['host']] += 1
            self.last_used[proxy['host']] = time.time()
            
            logger.debug(f"Proxy: {proxy['host']}:{proxy['port']} (uso #{self.proxy_usage[proxy['host']]})")
            
            return proxy

    def mark_failed(self, proxy):
        with self.lock:
            key = f"{proxy['host']}:{proxy['port']}"
            self.failed_proxies.add(key)
            logger.warning(f"Proxy falhou: {key}")

    def has_proxies(self):
        return len(self.proxies) > 0

    def get_active_count(self):
        return len([p for p in self.proxies if f"{p['host']}:{p['port']}" not in self.failed_proxies])

    def get_stats(self):
        stats = []
        for proxy in self.proxies:
            stats.append({
                'host': proxy['host'],
                'port': proxy['port'],
                'usage': self.proxy_usage.get(proxy['host'], 0),
                'failed': f"{proxy['host']}:{proxy['port']}" in self.failed_proxies
            })
        return stats
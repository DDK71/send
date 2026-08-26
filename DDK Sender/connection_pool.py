"""
Connection Pool Manager - DDK v4.0
Gerencia pool de conexões SMTP reutilizáveis para máxima velocidade
"""

import smtplib
import socket
import ssl
import time
import threading
import socks
from datetime import datetime
from collections import defaultdict

from config import (
    ENABLE_CONNECTION_POOLING,
    MAX_REUSES_PER_CONNECTION,
    CONNECTION_POOL_TIMEOUT,
    SMTP_TIMEOUT,
    PROXY_TYPE,
    USE_PROXY,
)
from utils import logger


class ProxiedSMTP(smtplib.SMTP):
    def __init__(self, host, port=587, timeout=SMTP_TIMEOUT, proxy=None):
        self._proxy = proxy
        self._timeout = timeout
        super().__init__(host=host, port=port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        if self._proxy:
            proxy_types = {"socks4": socks.SOCKS4, "socks5": socks.SOCKS5, "http": socks.HTTP}
            sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            sock.set_proxy(
                proxy_type=proxy_types.get(PROXY_TYPE, socks.SOCKS5),
                addr=self._proxy["host"],
                port=int(self._proxy["port"]),
                username=self._proxy.get("username"),
                password=self._proxy.get("password"),
            )
            sock.settimeout(self._timeout)
            sock.connect((host, port))
            return sock
        return socket.create_connection((host, port), timeout)


class ProxiedSMTP_SSL(smtplib.SMTP_SSL):
    def __init__(self, host, port=465, timeout=SMTP_TIMEOUT, context=None, proxy=None):
        self._proxy = proxy
        self._timeout = timeout
        if context is None:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        super().__init__(host=host, port=port, timeout=timeout, context=context)

    def _get_socket(self, host, port, timeout):
        if self._proxy:
            proxy_types = {"socks4": socks.SOCKS4, "socks5": socks.SOCKS5, "http": socks.HTTP}
            sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            sock.set_proxy(
                proxy_type=proxy_types.get(PROXY_TYPE, socks.SOCKS5),
                addr=self._proxy["host"],
                port=int(self._proxy["port"]),
                username=self._proxy.get("username"),
                password=self._proxy.get("password"),
            )
            sock.settimeout(self._timeout)
            sock.connect((host, port))
            return self.context.wrap_socket(sock, server_hostname=host, do_handshake_on_connect=True)
        return super()._get_socket(host, port, timeout)


class PooledConnection:
    """Wrapper para conexão reutilizável"""
    def __init__(self, connection, account_key, proxy=None):
        self.connection = connection
        self.account_key = account_key
        self.proxy = proxy
        self.reuse_count = 0
        self.created_at = time.time()
        self.last_used = time.time()
        self.lock = threading.Lock()

    def can_reuse(self):
        """Verifica se conexão pode ser reutilizada"""
        if self.reuse_count >= MAX_REUSES_PER_CONNECTION:
            return False
        if time.time() - self.last_used > CONNECTION_POOL_TIMEOUT:
            return False
        return True

    def mark_used(self):
        """Marca conexão como usada"""
        with self.lock:
            self.reuse_count += 1
            self.last_used = time.time()

    def close(self):
        """Fecha conexão"""
        try:
            self.connection.quit()
        except:
            try:
                self.connection.close()
            except:
                pass

    def __str__(self):
        return f"PooledConn({self.account_key}, reused={self.reuse_count})"


class ConnectionPool:
    """Pool gerenciador de conexões SMTP"""
    def __init__(self):
        self.pools = defaultdict(list)  # {account_key: [PooledConnection, ...]}
        self.lock = threading.Lock()
        self.total_created = 0
        self.total_reused = 0

    def get_connection(self, account_key, proxy=None, create_func=None):
        """
        Obtém conexão do pool ou cria nova
        
        Args:
            account_key: Chave única da conta (login@host:port)
            proxy: Proxy a usar (se houver)
            create_func: Função que cria nova conexão
        """
        if not ENABLE_CONNECTION_POOLING:
            # Desabilitado: sempre cria nova
            return create_func(), None

        with self.lock:
            available = self.pools.get(account_key, [])
            
            # Limpa conexões expiradas
            available = [c for c in available if c.can_reuse()]
            self.pools[account_key] = available

            if available:
                pooled = available.pop(0)
                pooled.mark_used()
                self.total_reused += 1
                logger.debug(f"Pool reuse: {pooled} (reusos: {pooled.reuse_count})")
                return pooled.connection, pooled
            else:
                # Cria nova conexão
                conn = create_func()
                pooled = PooledConnection(conn, account_key, proxy)
                self.total_created += 1
                logger.debug(f"Pool new: {pooled}")
                return conn, pooled

    def return_connection(self, account_key, pooled):
        """
        Retorna conexão ao pool para reutilização
        """
        if not ENABLE_CONNECTION_POOLING or pooled is None:
            return

        if pooled.can_reuse():
            with self.lock:
                self.pools[account_key].append(pooled)
                logger.debug(f"Pool returned: {pooled}")
        else:
            pooled.close()

    def close_all(self):
        """Fecha todas as conexões"""
        with self.lock:
            for account_key, connections in self.pools.items():
                for pooled in connections:
                    pooled.close()
            self.pools.clear()

    def get_stats(self):
        """Retorna estatísticas de uso"""
        return {
            "total_created": self.total_created,
            "total_reused": self.total_reused,
            "efficiency": (
                (self.total_reused / (self.total_created + self.total_reused) * 100)
                if (self.total_created + self.total_reused) > 0
                else 0
            ),
        }

"""
Gerenciador SMTP com Logs Transparentes e Detecção de Ratelimit
"""

import smtplib
import socket
import ssl
import time
import os
import threading
import socks
from datetime import datetime

from config import (
    SMTP_FILE,
    SMTP_TIMEOUT,
    SMTP_RETRY_ATTEMPTS,
    SMTP_RETRY_DELAY,
    USE_TLS,
    USE_SSL,
    USE_PROXY,
    MAX_EMAILS_PER_SMTP,
    PROXY_TYPE,
    MAX_FAILURES_BEFORE_REMOVE,
    REMOVE_SMTP_ON_FAIL,
    SAVE_REMOVED_SMTPS,
    REMOVED_SMTP_FILE,
    AUTO_CLEAN_INPUT_FILE,
)
from utils import load_file_lines, logger, Display


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


class SMTPAccount:
    def __init__(self, host, port, login, password, raw_line="", manager=None):
        self.host = host.strip()
        self.port = int(port.strip())
        self.login = login.strip()
        self.password = password.strip()
        self.raw_line = raw_line.strip()
        self.manager = manager
        self.sent_count = 0
        self.failed_count = 0
        self.consecutive_failures = 0
        self.is_active = True
        self.removed = False
        self.last_used = 0
        self.last_error = None
        self.lock = threading.Lock()
        self.ratelimited_until = 0

    def __str__(self):
        return f"{self.login}@{self.host}:{self.port}"

    def __hash__(self):
        return hash((self.host, self.port, self.login))

    def __eq__(self, other):
        if isinstance(other, SMTPAccount):
            return self.host == other.host and self.port == other.port and self.login == other.login
        return False

    def increment_sent(self):
        with self.lock:
            self.sent_count += 1
            self.consecutive_failures = 0
            self.last_used = time.time()
            if self.sent_count >= MAX_EMAILS_PER_SMTP:
                self.is_active = False
                logger.info(f"SMTP {self} pausado (limite de {MAX_EMAILS_PER_SMTP} envios)")

    def mark_ratelimited(self, duration=300):
        with self.lock:
            self.ratelimited_until = time.time() + duration
            self.is_active = False
            logger.warning(f"RATELIMIT: {self} pausado por {duration}s")

    def is_ratelimited(self):
        return time.time() < self.ratelimited_until

    def mark_removed(self, reason):
        with self.lock:
            if not self.removed:
                self.removed = True
                self.is_active = False
                self.last_error = reason
                logger.warning(f"SMTP REMOVIDO: {self} | {reason}")
                if self.manager:
                    self.manager.handle_account_removal(self, reason)

    def increment_failed(self, error_msg=None):
        with self.lock:
            if self.removed:
                return
            self.failed_count += 1
            self.consecutive_failures += 1
            self.last_error = error_msg
            if REMOVE_SMTP_ON_FAIL and self.consecutive_failures >= MAX_FAILURES_BEFORE_REMOVE:
                self.mark_removed(f"{self.consecutive_failures}x falhas: {error_msg}")

    def reactivate(self):
        with self.lock:
            if not self.removed:
                self.sent_count = 0
                self.is_active = True
                self.consecutive_failures = 0
                self.ratelimited_until = 0


class SMTPManager:
    def __init__(self, proxy_manager=None):
        self.accounts = []
        self.proxy_manager = proxy_manager
        self.lock = threading.Lock()
        self.file_lock = threading.Lock()
        self.current_index = 0
        self.removed_accounts = []

    def load_accounts(self, filepath=None):
        filepath = filepath or SMTP_FILE
        lines = load_file_lines(filepath)
        self.accounts = []
        for line in lines:
            parts = line.strip().split("|")
            if len(parts) == 4:
                acc = SMTPAccount(parts[0], parts[1], parts[2], parts[3], raw_line=line, manager=self)
                self.accounts.append(acc)
        Display.info(f"Carregadas {len(self.accounts)} contas SMTP")
        return len(self.accounts)

    def handle_account_removal(self, account, reason):
        Display.warning(f"SMTP Removido: {account} | {reason}")
        self.removed_accounts.append(account)
        
        if SAVE_REMOVED_SMTPS:
            try:
                os.makedirs(os.path.dirname(REMOVED_SMTP_FILE), exist_ok=True)
                with self.file_lock:
                    with open(REMOVED_SMTP_FILE, "a", encoding="utf-8") as f:
                        f.write(f"{account.raw_line} # {reason} - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
            except Exception as e:
                logger.error(f"Erro ao salvar: {e}")

        if AUTO_CLEAN_INPUT_FILE and os.path.exists(SMTP_FILE):
            try:
                with self.file_lock:
                    with open(SMTP_FILE, "r", encoding="utf-8") as f:
                        all_lines = f.readlines()
                    cleaned = [l for l in all_lines if account.raw_line not in l.strip()]
                    with open(SMTP_FILE, "w", encoding="utf-8") as f:
                        f.writelines(cleaned)
                    logger.info(f"SMTP removido do arquivo: {account}")
            except Exception as e:
                logger.error(f"Erro ao limpar arquivo: {e}")

    def get_account(self, exclude=None):
        with self.lock:
            exclude = exclude or set()
            available = [
                a for a in self.accounts
                if a.is_active and not a.removed and not a.is_ratelimited() and a not in exclude
            ]
            if not available:
                self._reactivate_eligible()
                available = [
                    a for a in self.accounts
                    if a.is_active and not a.removed and not a.is_ratelimited() and a not in exclude
                ]
            if not available:
                return None
            account = available[self.current_index % len(available)]
            self.current_index += 1
            return account

    def _reactivate_eligible(self):
        for account in self.accounts:
            if not account.removed and not account.is_ratelimited():
                account.reactivate()

    def reactivate_all(self):
        with self.lock:
            self._reactivate_eligible()

    def create_connection(self, account, proxy=None):
        connection = None
        use_ssl_direct = account.port == 465

        try:
            if use_ssl_direct:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                connection = ProxiedSMTP_SSL(
                    host=account.host,
                    port=account.port,
                    timeout=SMTP_TIMEOUT,
                    context=ctx,
                    proxy=proxy,
                )
                connection.ehlo()
            else:
                connection = ProxiedSMTP(
                    host=account.host,
                    port=account.port,
                    timeout=SMTP_TIMEOUT,
                    proxy=proxy,
                )
                connection.ehlo()
                if connection.has_extn("STARTTLS") or account.port in (587, 25, 2525):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    connection.starttls(context=ctx)
                    connection.ehlo()

            connection.login(account.login, account.password)
            logger.debug(f"Conexao OK: {account}")
            return connection, "OK"

        except smtplib.SMTPAuthenticationError as e:
            account.mark_removed(f"Auth: {e}")
            return None, f"AUTH_FAIL: {e}"
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)[:80]}"
            account.increment_failed(err_msg)
            return None, err_msg

    def send_email(self, account, from_email, to_email, raw_message, proxy=None):
        connection, err_detail = self.create_connection(account, proxy)
        if connection is None:
            return False, err_detail

        try:
            result = connection.sendmail(from_email, [to_email], raw_message)
            
            if result:
                refused = list(result.keys())
                logger.warning(f"Recusados: {refused}")
                return False, f"RECUSADOS: {refused}"
            
            account.increment_sent()
            logger.info(f"ENVIADO: {to_email} via {account}")
            return True, "OK"

        except smtplib.SMTPRecipientsRefused as e:
            return False, f"RECIPIENT_REFUSED: {e}"
        except smtplib.SMTPSenderRefused as e:
            account.mark_removed(f"Sender: {e}")
            return False, f"SENDER_REFUSED: {e}"
        except smtplib.SMTPDataError as e:
            err_str = str(e)
            if "4.7.1" in err_str or "ratelimit" in err_str.lower() or "exceeded" in err_str.lower():
                account.mark_ratelimited(300)
                return False, f"RATELIMIT: {err_str[:60]}"
            account.increment_failed(err_str)
            return False, f"DATA_ERROR: {err_str[:60]}"
        except smtplib.SMTPServerDisconnected as e:
            return False, f"DISCONNECTED: {e}"
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)[:80]}"
            account.increment_failed(err_msg)
            return False, err_msg
        finally:
            try:
                connection.quit()
            except:
                try:
                    connection.close()
                except:
                    pass

    def get_active_count(self):
        return len([a for a in self.accounts if a.is_active and not a.removed and not a.is_ratelimited()])

    def get_removed_count(self):
        return len(self.removed_accounts)

    def save_removed_smtps(self):
        """Método de compatibilidade - salvamento já feito em handle_account_removal"""
        if self.removed_accounts:
            logger.info(f"{len(self.removed_accounts)} SMTPs removidos durante o envio")
            logger.info(f"Backup salvo em: {REMOVED_SMTP_FILE}")
        else:
            logger.info("Nenhum SMTP removido durante o envio")

    def get_stats(self):
        return {
            "total_accounts": len(self.accounts),
            "active_accounts": self.get_active_count(),
            "removed_accounts": len(self.removed_accounts),
            "total_sent": sum(a.sent_count for a in self.accounts),
            "total_failed": sum(a.failed_count for a in self.accounts),
        }
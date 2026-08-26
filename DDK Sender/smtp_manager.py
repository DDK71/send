"""
Gerenciador SMTP com Logs Transparentes, Detecção de Ratelimit e Connection Pooling
v4.0: Reactivation com cooldown, Ratelimit inteligente, Diferenciação Proxy vs SMTP
"""

import smtplib
import socket
import ssl
import time
import os
import threading
import socks
import re
from datetime import datetime

from config import (
    SMTP_FILE,
    SMTP_TIMEOUT,
    SMTP_RETRY_ATTEMPTS,
    SMTP_RETRY_DELAY,
    SMTP_RETRY_BACKOFF,
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
    REACTIVATION_COOLDOWN,
    RATELIMIT_BACKOFF,
    ENABLE_CONNECTION_POOLING,
)
from utils import load_file_lines, logger, Display
from connection_pool import ProxiedSMTP, ProxiedSMTP_SSL, ConnectionPool


class ProxyError(Exception):
    """Erro específico de proxy (não de SMTP)"""
    pass


class RatelimitError(Exception):
    """Erro de ratelimit (com código)"""
    def __init__(self, message, error_code="default"):
        super().__init__(message)
        self.error_code = error_code


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
        self.last_reactivated = 0  # NOVO: timestamp de última reciclagem
        self.lock = threading.Lock()
        self.ratelimited_until = 0
        self.ratelimit_error_code = "default"

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

    def mark_ratelimited(self, error_code="default", duration=None):
        """Marca como ratelimitado com backoff inteligente"""
        with self.lock:
            if duration is None:
                duration = RATELIMIT_BACKOFF.get(error_code, RATELIMIT_BACKOFF["default"])
            
            self.ratelimited_until = time.time() + duration
            self.ratelimit_error_code = error_code
            self.is_active = False
            logger.warning(f"RATELIMIT [{error_code}]: {self} pausado por {duration}s")

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

    def increment_failed(self, error_msg=None, is_proxy_error=False):
        """Incrementa falhas, diferenciando erros de proxy"""
        with self.lock:
            if self.removed:
                return
            
            if is_proxy_error:
                # Não conta contra SMTP se for erro de proxy
                logger.debug(f"PROXY ERROR (não conta contra SMTP): {self} | {error_msg}")
                return
            
            self.failed_count += 1
            self.consecutive_failures += 1
            self.last_error = error_msg
            if REMOVE_SMTP_ON_FAIL and self.consecutive_failures >= MAX_FAILURES_BEFORE_REMOVE:
                self.mark_removed(f"{self.consecutive_failures}x falhas: {error_msg}")

    def reactivate(self):
        """Recicla SMTP com cooldown"""
        with self.lock:
            now = time.time()
            # NOVO: Respeita cooldown mínimo
            if now - self.last_reactivated < REACTIVATION_COOLDOWN:
                return False
            
            if not self.removed:
                self.sent_count = 0
                self.is_active = True
                self.consecutive_failures = 0
                self.ratelimited_until = 0
                self.last_reactivated = now
                logger.info(f"SMTP reciclado: {self}")
                return True
            return False


class SMTPManager:
    def __init__(self, proxy_manager=None):
        self.accounts = []
        self.proxy_manager = proxy_manager
        self.lock = threading.Lock()
        self.file_lock = threading.Lock()
        self.current_index = 0
        self.removed_accounts = []
        self.connection_pool = ConnectionPool()  # NOVO: Pool de conexões
        self.last_removal_time = 0  # NOVO: Controla spike de removals

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
        """Trata remoção de conta com intervalo mínimo"""
        now = time.time()
        
        # NOVO: Evita spike de removals
        if now - self.last_removal_time < 30:
            logger.debug(f"Removal bloqueado (cooldown): {account}")
            return
        
        self.last_removal_time = now
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
        """Recicla contas elegíveis (respeitando cooldown)"""
        for account in self.accounts:
            if not account.removed and not account.is_ratelimited():
                account.reactivate()

    def reactivate_all(self):
        with self.lock:
            self._reactivate_eligible()

    def create_connection(self, account, proxy=None):
        """Cria conexão SMTP com diferenciação de erros"""
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
        except (socket.timeout, TimeoutError, OSError) as e:
            if "Proxy" in str(e) or "SOCKS" in str(e):
                raise ProxyError(f"Proxy timeout: {e}")
            err_msg = f"{type(e).__name__}: {str(e)[:80]}"
            account.increment_failed(err_msg)
            return None, err_msg
        except Exception as e:
            if "Proxy" in str(e) or "SOCKS" in str(e):
                raise ProxyError(f"Proxy error: {e}")
            err_msg = f"{type(e).__name__}: {str(e)[:80]}"
            account.increment_failed(err_msg)
            return None, err_msg

    def send_email(self, account, from_email, to_email, raw_message, proxy=None):
        """Envia email com retry automático e pooling"""
        
        for attempt in range(1, SMTP_RETRY_ATTEMPTS + 1):
            try:
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
                    # NOVO: Detecta código de erro específico
                    error_code = self._extract_error_code(err_str)
                    if error_code in RATELIMIT_BACKOFF or "ratelimit" in err_str.lower() or "exceeded" in err_str.lower():
                        account.mark_ratelimited(error_code)
                        return False, f"RATELIMIT [{error_code}]: {err_str[:60]}"
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

            except ProxyError as e:
                logger.warning(f"PROXY ERROR (attempt {attempt}): {e}")
                # Marca proxy como ruim, não SMTP
                if self.proxy_manager:
                    self.proxy_manager.mark_failed(proxy)
                
                # Retry com proxy diferente
                if attempt < SMTP_RETRY_ATTEMPTS:
                    wait = SMTP_RETRY_DELAY * (2 ** attempt) if SMTP_RETRY_BACKOFF else SMTP_RETRY_DELAY
                    time.sleep(wait)
                    # Pega nova proxy
                    if self.proxy_manager and self.proxy_manager.has_proxies():
                        proxy = self.proxy_manager.get_proxy()
                else:
                    return False, f"PROXY_FAIL: {e}"
            except Exception as e:
                logger.error(f"Erro inesperado no send_email: {e}")
                return False, str(e)
        
        return False, "Max retries exceeded"

    @staticmethod
    def _extract_error_code(error_str):
        """Extrai código de erro SMTP (ex: 4.7.1) da mensagem"""
        match = re.search(r"\b(\d{1}\.\d{1}\.\d{1})\b", error_str)
        if match:
            return match.group(1)
        if "429" in error_str:
            return "429"
        return "default"

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
            "pool_stats": self.connection_pool.get_stats(),
        }

    def close_pools(self):
        """Fecha todos os pools de conexão"""
        self.connection_pool.close_all()

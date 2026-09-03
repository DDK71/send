"""
Gerenciador SMTP v5.0 - Simplificado (delegação para Go Engine)
Mantém estado de contas, ratelimit e reativação
"""
import threading
import time
import os
from datetime import datetime

from config import (
    SMTP_FILE, MAX_EMAILS_PER_SMTP, MAX_FAILURES_BEFORE_REMOVE,
    REMOVE_SMTP_ON_FAIL, SAVE_REMOVED_SMTPS, REMOVED_SMTP_FILE,
    RATELIMIT_BACKOFF, REACTIVATION_INTERVAL, MASK_CREDENTIALS_IN_LOGS,
)
from utils import load_file_lines, logger, mask_credential_string, atomic_write


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
        self.last_reactivated = 0

    def __str__(self):
        return f"{self.login}@{self.host}:{self.port}"

    def __hash__(self):
        return hash((self.host, self.port, self.login))

    def __eq__(self, other):
        if isinstance(other, SMTPAccount):
            return self.host == other.host and self.port == other.port and self.login == other.login
        return False

    def to_go_dict(self):
        return {
            "host": self.host,
            "port": self.port,
            "username": self.login,
            "password": self.password,
            "use_ssl": self.port == 465,
        }

    def increment_sent(self, count=1):
        with self.lock:
            self.sent_count += count
            self.consecutive_failures = 0
            self.last_used = time.time()
            if self.sent_count >= MAX_EMAILS_PER_SMTP:
                self.is_active = False
                logger.info(f"SMTP {self} pausado (limite {MAX_EMAILS_PER_SMTP})")

    def mark_ratelimited(self, error_code="default"):
        duration = RATELIMIT_BACKOFF.get(error_code, RATELIMIT_BACKOFF.get("default", 300))
        with self.lock:
            self.ratelimited_until = time.time() + duration
            self.is_active = False
            logger.warning(f"RATELIMIT: {self} pausado {duration}s (código: {error_code})")

    def is_ratelimited(self):
        return time.time() < self.ratelimited_until

    def mark_removed(self, reason):
        with self.lock:
            if not self.removed:
                self.removed = True
                self.is_active = False
                self.last_error = reason
                safe_str = mask_credential_string(self.raw_line) if MASK_CREDENTIALS_IN_LOGS else self.raw_line
                logger.warning(f"SMTP REMOVIDO: {safe_str} | {reason}")
                if self.manager:
                    self.manager.mark_for_removal(self, reason)

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
                self.last_reactivated = time.time()


class SMTPManager:
    def __init__(self, proxy_manager=None):
        self.accounts = []
        self.proxy_manager = proxy_manager
        self.lock = threading.Lock()
        self.current_index = 0
        self.removed_accounts = []
        self.removal_queue = []

    def load_accounts(self, filepath=None):
        filepath = filepath or SMTP_FILE
        lines = load_file_lines(filepath)
        self.accounts = []
        for line in lines:
            parts = line.strip().split("|")
            if len(parts) == 4:
                acc = SMTPAccount(parts[0], parts[1], parts[2], parts[3], raw_line=line, manager=self)
                self.accounts.append(acc)
        # Silenciado do terminal de envio ativo para manter tela organizada
        logger.info(f"Carregadas {len(self.accounts)} contas SMTP no SMTPManager")
        return len(self.accounts)

    def mark_for_removal(self, account, reason):
        with self.lock:
            self.removed_accounts.append(account)
            self.removal_queue.append((account, reason, datetime.now()))

    def flush_removals(self):
        if not self.removal_queue:
            return

        removed_lines = {acc.raw_line for acc, _, _ in self.removal_queue}

        if SAVE_REMOVED_SMTPS:
            try:
                os.makedirs(os.path.dirname(REMOVED_SMTP_FILE), exist_ok=True)
                with open(REMOVED_SMTP_FILE, "a", encoding="utf-8") as f:
                    f.write(f"\n# Sessão: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                    for acc, reason, ts in self.removal_queue:
                        f.write(f"{acc.raw_line} # {reason} [{ts.strftime('%H:%M:%S')}]\n")
            except Exception as e:
                logger.error(f"Erro ao salvar removidos: {e}")

        if os.path.exists(SMTP_FILE):
            try:
                with open(SMTP_FILE, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                cleaned = [l for l in all_lines if l.strip() not in removed_lines]
                atomic_write(SMTP_FILE, "".join(cleaned))
            except Exception as e:
                logger.error(f"Erro ao limpar arquivo: {e}")

        self.removal_queue.clear()

    def get_account(self, exclude=None):
        with self.lock:
            exclude = exclude or set()
            available = [a for a in self.accounts
                         if a.is_active and not a.removed
                         and not a.is_ratelimited() and a not in exclude]
            if not available:
                self._reactivate_eligible()
                available = [a for a in self.accounts
                             if a.is_active and not a.removed
                             and not a.is_ratelimited() and a not in exclude]
            if not available:
                return None
            account = available[self.current_index % len(available)]
            self.current_index += 1
            return account

    def _reactivate_eligible(self):
        now = time.time()
        for account in self.accounts:
            if (not account.removed and not account.is_ratelimited()
                    and (now - account.last_reactivated) > REACTIVATION_INTERVAL):
                account.reactivate()

    def reactivate_all(self):
        with self.lock:
            self._reactivate_eligible()

    def get_active_count(self):
        return len([a for a in self.accounts
                    if a.is_active and not a.removed and not a.is_ratelimited()])

    def get_stats(self):
        return {
            "total_accounts": len(self.accounts),
            "active_accounts": self.get_active_count(),
            "removed_accounts": len(self.removed_accounts),
            "total_sent": sum(a.sent_count for a in self.accounts),
            "total_failed": sum(a.failed_count for a in self.accounts),
        }
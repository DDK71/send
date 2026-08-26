"""
Gerenciador de Threads com Rotação Forçada de Proxy
"""

import time
import random
import threading
from queue import Queue, Empty

from config import (
    MAX_THREADS,
    DELAY_BETWEEN_EMAILS,
    DELAY_JITTER_MIN,
    DELAY_JITTER_MAX,
    WARMUP_START,
    WARMUP_INCREMENT,
    WARMUP_PAUSE,
    USE_PROXY,
)
from utils import Display, logger, validate_email, get_current_ip, extract_domain


class SendTask:
    def __init__(self, to_email, subject, html_body=None, template_vars=None):
        self.to_email = to_email
        self.subject = subject
        self.html_body = html_body
        self.template_vars = template_vars or {}


class ThreadManager:
    MAX_SMTP_RETRIES_PER_EMAIL = 3

    def __init__(self, smtp_manager, email_builder, proxy_manager=None):
        self.smtp_manager = smtp_manager
        self.email_builder = email_builder
        self.proxy_manager = proxy_manager
        self.stop_event = threading.Event()
        self.task_queue = Queue()
        self._local_ip = None
        self._ip_lock = threading.Lock()

    def _get_local_ip_cached(self):
        with self._ip_lock:
            if self._local_ip is None:
                self._local_ip = get_current_ip()
        return self._local_ip

    def _get_ip_info(self, proxy=None):
        if proxy:
            return f"Proxy:{proxy['host']}:{proxy['port']}"
        return f"IP:{self._get_local_ip_cached()}"

    def _apply_delay(self):
        if DELAY_BETWEEN_EMAILS > 0:
            jitter = random.uniform(DELAY_JITTER_MIN, DELAY_JITTER_MAX)
            time.sleep(DELAY_BETWEEN_EMAILS * jitter)

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                task = self.task_queue.get_nowait()
            except Empty:
                break
            try:
                self._send_single(task)
            except Exception as e:
                logger.error(f"Erro no processamento de {task.to_email}: {e}")
            finally:
                self.task_queue.task_done()

    def _send_single(self, task):
        if not validate_email(task.to_email):
            Display.error("VALIDACAO", task.to_email, "Formato invalido")
            return False

        max_attempts = min(len(self.smtp_manager.accounts), self.MAX_SMTP_RETRIES_PER_EMAIL)
        tried_accounts = set()
        success = False
        last_error = "Falha no envio"
        last_smtp_str = "N/A"
        last_ip_info = None

        for attempt in range(1, max_attempts + 1):
            if self.stop_event.is_set():
                break

            # FORÇA NOVA PROXY A CADA TENTATIVA
            proxy = None
            if USE_PROXY and self.proxy_manager and self.proxy_manager.has_proxies():
                proxy = self.proxy_manager.get_proxy()  # Sempre pega proxy diferente

            account = self.smtp_manager.get_account(exclude=tried_accounts)
            if account is None:
                last_error = "Sem SMTPs disponiveis"
                break

            tried_accounts.add(account)
            last_smtp_str = str(account)
            last_ip_info = self._get_ip_info(proxy)

            from_email = account.login
            from_domain = extract_domain(from_email)
            brand_name = self.email_builder.extract_brand_name(from_email)

            template_vars = {
                "email": task.to_email,
                "to": task.to_email,
                "domain": task.to_email.split("@")[1] if "@" in task.to_email else "",
                "brand_name": brand_name,
                "brand_domain": from_domain,
                "brand_upper": brand_name.upper(),
                "from_email": from_email,
                "from_domain": from_domain,
                "date": time.strftime("%d/%m/%Y"),
                "time": time.strftime("%H:%M"),
                "random": str(random.randint(10000, 99999)),
                "unsubscribe_id": str(random.randint(100000, 999999)),
                **task.template_vars,
            }

            raw_message = self.email_builder.build_raw(
                from_email=from_email,
                to_email=task.to_email,
                subject=task.subject,
                html_body=task.html_body,
                template_vars=template_vars,
            )

            success, err_detail = self.smtp_manager.send_email(
                account=account,
                from_email=from_email,
                to_email=task.to_email,
                raw_message=raw_message,
                proxy=proxy,
            )

            if success:
                break
            else:
                last_error = err_detail
                logger.warning(f"Tentativa {attempt} falhou: {err_detail}")

        if success:
            Display.success(last_smtp_str, task.to_email, last_ip_info)
            logger.info(f"ENVIADO: {task.to_email} | SMTP: {last_smtp_str} | {last_ip_info}")
        else:
            Display.error(last_smtp_str, task.to_email, last_error, last_ip_info)
            logger.error(f"FALHA: {task.to_email} | Erro: {last_error}")

        self._apply_delay()
        return success

    def send_batch(self, tasks, num_threads=None):
        num_threads = num_threads or MAX_THREADS
        Display.total = len(tasks)
        self.stop_event.clear()

        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
                self.task_queue.task_done()
            except Empty:
                break

        for task in tasks:
            self.task_queue.put(task)

        Display.info(f"Fila: {len(tasks)} emails | Threads: {num_threads}")

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

    def send_warmup(self, tasks, num_threads=None):
        num_threads = num_threads or min(5, MAX_THREADS)
        current_size = WARMUP_START
        tasks_remaining = list(tasks)
        round_num = 0

        Display.info("Modo WARMUP ativado")
        Display.total = len(tasks)
        self.stop_event.clear()

        while tasks_remaining and not self.stop_event.is_set():
            round_num += 1
            batch = tasks_remaining[:current_size]
            tasks_remaining = tasks_remaining[current_size:]

            Display.info(f"Round {round_num}: {len(batch)} emails")

            for task in batch:
                self.task_queue.put(task)

            threads = []
            for _ in range(min(num_threads, len(batch))):
                t = threading.Thread(target=self._worker, daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            current_size = current_size + WARMUP_INCREMENT
            self.smtp_manager.reactivate_all()

            if tasks_remaining:
                Display.info(f"Pausa ({WARMUP_PAUSE}s)...")
                time.sleep(WARMUP_PAUSE)

    def stop(self):
        self.stop_event.set()
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
                self.task_queue.task_done()
            except Empty:
                break
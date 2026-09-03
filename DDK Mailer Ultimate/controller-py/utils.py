"""
Utilitários, Logger e Interface Visual — DDK v5.0
"""
import os
import re
import random
import string
import time
import logging
import threading
import tempfile
import shutil
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box

console = Console(
    force_terminal=True,
    color_system="standard",
    highlight=False,
    soft_wrap=True,
    width=120,
)


# ── Logger ─────────────────────────────────────────────
def setup_logger(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"mailer_{timestamp}.log")

    logger = logging.getLogger("MassMailer")
    logger.setLevel(logging.DEBUG)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


logger = setup_logger()


# ── Máscara de Credenciais ─────────────────────────────
def mask_password(password):
    if not password or len(password) < 4:
        return "****"
    return password[:2] + "*" * (len(password) - 4) + password[-2:]


def mask_credential_string(cred_str):
    """Mascara host|port|login|password"""
    parts = cred_str.split("|")
    if len(parts) == 4:
        parts[3] = mask_password(parts[3])
        return "|".join(parts)
    return cred_str


# ── IP Cache ───────────────────────────────────────────
_cached_ip = None
_cached_ip_lock = threading.Lock()


def get_current_ip():
    global _cached_ip
    with _cached_ip_lock:
        if _cached_ip is not None:
            return _cached_ip
    try:
        import urllib.request
        response = urllib.request.urlopen("https://api.ipify.org", timeout=5)
        ip = response.read().decode("utf-8")
    except Exception:
        ip = "Desconhecido"
    with _cached_ip_lock:
        _cached_ip = ip
    return ip


# ── Escrita Atômica de Arquivos ────────────────────────
def atomic_write(filepath, content, encoding="utf-8"):
    """Escrita atômica: escreve em tempfile e renomeia (previne corrupção)"""
    dirname = os.path.dirname(filepath) or "."
    with tempfile.NamedTemporaryFile(
        mode="w", encoding=encoding, delete=False, dir=dirname, suffix=".tmp"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    shutil.move(tmp_path, filepath)


def atomic_append(filepath, line, encoding="utf-8"):
    """Append seguro com lock"""
    lock_path = filepath + ".lock"
    try:
        with open(filepath, "a", encoding=encoding) as f:
            f.write(line)
    except Exception as e:
        logger.error(f"Erro append em {filepath}: {e}")


# ── Display ────────────────────────────────────────────
class Display:
    sent = 0
    failed = 0
    total = 0
    _lock = threading.Lock()

    @staticmethod
    def reset():
        Display.sent = 0
        Display.failed = 0
        Display.total = 0

    @staticmethod
    def banner():
        console.clear()
        title = Text()
        title.append("⚡ DDK MASS MAILER v5.0 ⚡\n", style="bold cyan")
        title.append("Python + Go Hybrid Engine • Inbox Optimized • RFC 8058", style="dim white")
        panel = Panel(
            Align.center(title),
            box=box.DOUBLE_EDGE,
            border_style="bright_blue",
            padding=(1, 2),
        )
        console.print(panel)

    @staticmethod
    def show_inputs_overview(smtp_count, proxy_count, emails_count, subjects_count):
        table = Table(title="📁 Recursos Carregados", box=box.ROUNDED, header_style="bold magenta")
        table.add_column("Recurso", justify="left", style="cyan")
        table.add_column("Quantidade", justify="right", style="bold green")
        table.add_row("Contas SMTP", f"{smtp_count}")
        table.add_row("Proxies", f"{proxy_count}" if proxy_count > 0 else "[yellow]0 (direto)[/yellow]")
        table.add_row("Destinatários", f"{emails_count}")
        table.add_row("Assuntos (rotação)", f"{subjects_count}")
        console.print(table)
        console.print()

    @staticmethod
    def show_campaign_summary(settings, smtp_count, total_emails):
        table = Table(title="⚙️ Configuração da Campanha", box=box.HEAVY_EDGE, header_style="bold cyan")
        table.add_column("Parâmetro", style="bold white")
        table.add_column("Valor", style="bright_yellow")
        table.add_row("Motor", "[green]Go Engine[/green]" if settings.get("use_go") else "[yellow]Python Fallback[/yellow]")
        table.add_row("Assuntos", settings.get("subjects_info", "1 fixo"))
        table.add_row("Destinatários", str(total_emails))
        table.add_row("SMTPs Ativos", str(smtp_count))
        table.add_row("Workers", str(settings["num_workers"]))
        table.add_row("Conns/SMTP", str(settings.get("conns_per_smtp", 3)))
        table.add_row("Delay (base)", f"{settings['delay']}s + jitter")
        table.add_row("Proxy", "[green]Ativo[/green]" if settings["use_proxy"] else "[dim]Off[/dim]")
        table.add_row("Spintax", "[green]Ativo[/green]" if settings.get("spintax") else "[dim]Off[/dim]")
        table.add_row("List-Unsubscribe", "[green]RFC 8058[/green]" if settings.get("list_unsub") else "[red]Off (risco)[/red]")
        table.add_row("Warmup", "[green]Ativo[/green]" if settings.get("warmup") else "[dim]Off[/dim]")
        console.print(table)
        console.print()

    @staticmethod
    def success(smtp_str, recipient, ip_info=None, latency=None):
        with Display._lock:
            Display.sent += 1
            ip = ip_info or ""
            lat = f"[dim]{latency:.0f}ms[/dim] │ " if latency else ""
            console.print(
                f"  [bold green]✔ INBOX [/bold green] │ "
                f"[white]{recipient:<35}[/white] │ "
                f"[cyan]{smtp_str:<30}[/cyan] │ "
                f"{lat}"
                f"[dim]{ip}[/dim] │ "
                f"[bold green]{Display.sent}[/bold green]/"
                f"[bold red]{Display.failed}[/bold red]/"
                f"[dim]{Display.total}[/dim]",
                highlight=False,
            )

    @staticmethod
    def error(smtp_str, recipient, error_msg, ip_info=None):
        with Display._lock:
            Display.failed += 1
            ip = ip_info or ""
            console.print(
                f"  [bold red]✖ FALHA [/bold red] │ "
                f"[white]{recipient:<35}[/white] │ "
                f"[yellow]{smtp_str:<30}[/yellow] │ "
                f"[dim]{ip}[/dim] │ "
                f"[red]{error_msg[:60]}[/red]",
                highlight=False,
            )

    @staticmethod
    def info(msg):
        console.print(f"  [bold blue]ℹ[/bold blue] {msg}", highlight=False)

    @staticmethod
    def warning(msg):
        console.print(f"  [bold yellow]⚠[/bold yellow] [yellow]{msg}[/yellow]", highlight=False)

    @staticmethod
    def summary(elapsed_time):
        total_attempted = Display.sent + Display.failed
        rate = (Display.sent / total_attempted * 100) if total_attempted > 0 else 0
        speed = (Display.sent / elapsed_time * 60) if elapsed_time > 0 else 0
        grid = Table(box=box.DOUBLE_EDGE, border_style="bright_green", title="📊 RESULTADO FINAL")
        grid.add_column("Métrica", style="bold white")
        grid.add_column("Valor", justify="right", style="bold cyan")
        grid.add_row("Enviados", f"[bold green]{Display.sent}[/bold green]")
        grid.add_row("Falhas", f"[bold red]{Display.failed}[/bold red]")
        grid.add_row("Taxa de Sucesso", f"{rate:.1f}%")
        grid.add_row("Tempo Total", f"{elapsed_time:.1f}s")
        grid.add_row("Velocidade", f"{speed:.0f} emails/min")
        console.print()
        console.print(grid)


# ── Funções Utilitárias ────────────────────────────────
def load_file_lines(filepath):
    if not os.path.exists(filepath):
        return []
    for enc in ["utf-8", "latin-1", "cp1252"]:
        try:
            with open(filepath, "r", encoding=enc) as f:
                return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        except (UnicodeDecodeError, Exception):
            continue
    return []


def random_string(length=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def generate_message_id(domain):
    ts = int(time.time() * 1000)
    rand = random_string(14)
    return f"<{ts}.{rand}@{domain}>"


def extract_domain(email):
    match = re.search(r"@([a-zA-Z0-9.-]+)", email)
    return match.group(1) if match else "localhost"


def sanitize_html(html):
    return html.replace("\r\n", "\n")


def validate_email(email):
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email.strip()) is not None
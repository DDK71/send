#!/usr/bin/env python3
"""
DDK Mass Mailer v3.3 — Entry Point com SMTP Checker Integrado
"""

import sys
import os
import signal
import time
import random

from rich.prompt import Prompt, Confirm
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    MAX_THREADS,
    SMTP_FILE,
    PROXY_FILE,
    EMAIL_LIST_FILE,
    FROM_NAMES_FILE,
    SUBJECTS_FILE,
    HTML_TEMPLATE,
    DELAY_BETWEEN_EMAILS,
    ENABLE_SPINTAX,
    ENABLE_HASH_BUSTER,
    USE_PROXY,
)
from utils import (
    Display,
    logger,
    load_file_lines,
    validate_email,
    console,
)
from smtp_manager import SMTPManager
from email_builder import EmailBuilder
from proxy_manager import ProxyManager
from thread_manager import ThreadManager, SendTask
from smtp_checker import run_checker


def create_directories():
    for d in ["inputs", "templates", "logs", "attachments"]:
        os.makedirs(d, exist_ok=True)


def create_default_files():
    defaults = {
        SMTP_FILE: "# Formato: host|porta|login|senha\n",
        EMAIL_LIST_FILE: "# Um email por linha\n",
        SUBJECTS_FILE: "# Um assunto por linha (suporta Spintax)\n",
        FROM_NAMES_FILE: "# Um nome por linha (suporta Spintax)\n",
        PROXY_FILE: "# Formato: host:porta:login:senha\n",
    }
    for filepath, content in defaults.items():
        if not os.path.exists(filepath):
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)


def load_subjects():
    lines = load_file_lines(SUBJECTS_FILE)
    return lines if lines else ["Documento Importante"]


def show_main_menu():
    """Exibe o menu principal"""
    console.clear()
    title = Text()
    title.append("⚡ DDK MASS MAILER v3.3 ⚡\n", style="bold cyan")
    title.append("Multi-Thread • Multi-SMTP • Spintax • Hash Buster • Inbox Engine", style="dim white")

    console.print(Panel(
        Align.center(title),
        box=box.DOUBLE_EDGE,
        border_style="bright_blue",
        padding=(1, 2),
    ))

    menu = """
  [bold cyan][1][/bold cyan] 🚀  [bold white]Iniciar Envio em Massa[/bold white]
  [bold cyan][2][/bold cyan] 🔍  [bold white]Verificar SMTPs (Checker)[/bold white]
  [bold cyan][3][/bold cyan] 📊  [bold white]Verificar SMTPs + Limpar Mortos (Auto)[/bold white]
  [bold cyan][0][/bold cyan] ❌  [bold white]Sair[/bold white]
"""
    console.print(menu)

    choice = Prompt.ask("  Selecione uma opção", choices=["0", "1", "2", "3"], default="1")
    return choice


def run_sender():
    """Fluxo completo de envio"""
    smtp_lines = load_file_lines(SMTP_FILE)
    if not smtp_lines:
        Display.error("CONFIG", "Sistema", f"Nenhum SMTP em {SMTP_FILE}")
        return

    email_lines = load_file_lines(EMAIL_LIST_FILE)
    if not email_lines:
        Display.error("CONFIG", "Sistema", f"Nenhum email em {EMAIL_LIST_FILE}")
        return

    valid_emails = [e for e in email_lines if validate_email(e)]
    invalid_count = len(email_lines) - len(valid_emails)
    if invalid_count > 0:
        Display.warning(f"{invalid_count} emails inválidos removidos")

    proxy_lines = load_file_lines(PROXY_FILE)
    subjects = load_subjects()

    Display.show_inputs_overview(
        smtp_count=len(smtp_lines),
        proxy_count=len(proxy_lines),
        emails_count=len(valid_emails),
        subjects_count=len(subjects),
    )

    if len(subjects) > 1:
        Display.info(f"Assuntos em rotação ({len(subjects)}):")
        for i, subj in enumerate(subjects[:5], 1):
            console.print(f"    [dim]{i}.[/dim] [cyan]{subj}[/cyan]")
        if len(subjects) > 5:
            console.print(f"    [dim]... e mais {len(subjects) - 5}[/dim]")
    else:
        Display.info(f"Assunto: [cyan]{subjects[0]}[/cyan]")
    console.print()

    console.rule("[bold cyan]Configuração[/bold cyan]")

    use_template = Confirm.ask("📄 Usar template HTML?", default=True)
    html_body = None
    if not use_template:
        html_body = Prompt.ask("📝 Cole o HTML do corpo")

    reply_to = Prompt.ask("↩️  Reply-To [dim](Enter = pular)[/dim]", default="")
    num_threads = int(Prompt.ask("🧵 Threads", default=str(MAX_THREADS)))
    delay = float(Prompt.ask("⏱️  Delay (s)", default=str(DELAY_BETWEEN_EMAILS)))
    use_proxy = Confirm.ask("🔒 Usar proxies?", default=bool(proxy_lines))
    warmup = Confirm.ask("🔥 Modo warmup?", default=False)
    use_attachments = Confirm.ask("📎 Anexos?", default=False)
    spintax = Confirm.ask("🔀 Spintax?", default=ENABLE_SPINTAX)
    hash_buster = Confirm.ask("🛡️  Hash Buster?", default=ENABLE_HASH_BUSTER)

    import config
    config.DELAY_BETWEEN_EMAILS = delay
    config.USE_PROXY = use_proxy
    config.ENABLE_SPINTAX = spintax
    config.ENABLE_HASH_BUSTER = hash_buster

    settings = {
        "subjects_info": f"{len(subjects)} em rotação" if len(subjects) > 1 else subjects[0],
        "num_threads": num_threads,
        "delay": delay,
        "use_proxy": use_proxy,
        "warmup": warmup,
        "use_attachments": use_attachments,
        "spintax": spintax,
        "hash_buster": hash_buster,
    }

    proxy_manager = None
    if use_proxy:
        proxy_manager = ProxyManager(PROXY_FILE)
        if not proxy_manager.has_proxies():
            Display.warning("Nenhum proxy, enviando direto")
            config.USE_PROXY = False
            proxy_manager = None

    smtp_manager = SMTPManager(proxy_manager=proxy_manager)
    smtp_manager.load_accounts()

    if smtp_manager.get_active_count() == 0:
        Display.error("CONFIG", "SMTP", "Nenhum SMTP válido!")
        return

    email_builder = EmailBuilder()
    if use_attachments:
        email_builder.enable_attachments(True)
    if use_template and os.path.exists(HTML_TEMPLATE):
        email_builder.load_template(HTML_TEMPLATE)
    if os.path.exists(FROM_NAMES_FILE):
        email_builder.load_from_names(FROM_NAMES_FILE)

    thread_manager = ThreadManager(
        smtp_manager=smtp_manager,
        email_builder=email_builder,
        proxy_manager=proxy_manager,
    )

    def signal_handler(sig, frame):
        console.print("\n[bold yellow]Encerrando...[/bold yellow]")
        thread_manager.stop()

    signal.signal(signal.SIGINT, signal_handler)

    tasks = []
    for email_addr in valid_emails:
        subject = random.choice(subjects)
        tasks.append(SendTask(to_email=email_addr, subject=subject, html_body=html_body))

    console.print()
    Display.show_campaign_summary(settings, smtp_manager.get_active_count(), len(tasks))

    if not Confirm.ask("🚀 [bold green]Confirmar e iniciar?[/bold green]", default=True):
        Display.warning("Cancelado.")
        return

    console.rule("[bold green]Envio em Andamento[/bold green]")
    start_time = time.time()

    if warmup:
        thread_manager.send_warmup(tasks, num_threads=num_threads)
    else:
        thread_manager.send_batch(tasks, num_threads=num_threads)

    elapsed = time.time() - start_time
    Display.summary(elapsed)
    smtp_manager.save_removed_smtps()


def main():
    create_directories()
    create_default_files()

    while True:
        choice = show_main_menu()

        if choice == "0":
            console.print("\n  [dim]Até logo![/dim]\n")
            sys.exit(0)

        elif choice == "1":
            run_sender()
            console.print()
            Prompt.ask("  [dim]Pressione Enter para voltar ao menu[/dim]")

        elif choice == "2":
            proxy_lines = load_file_lines(PROXY_FILE)
            proxy_manager = None
            if proxy_lines:
                use_proxy = Confirm.ask("🔒 Usar proxies no checker?", default=True)
                if use_proxy:
                    proxy_manager = ProxyManager(PROXY_FILE)

            num_threads = int(Prompt.ask("🧵 Threads para verificação", default="10"))
            run_checker(proxy_manager=proxy_manager, num_threads=num_threads)
            console.print()
            Prompt.ask("  [dim]Pressione Enter para voltar ao menu[/dim]")

        elif choice == "3":
            proxy_lines = load_file_lines(PROXY_FILE)
            proxy_manager = None
            if proxy_lines:
                proxy_manager = ProxyManager(PROXY_FILE)

            num_threads = int(Prompt.ask("🧵 Threads para verificação", default="10"))
            run_checker(proxy_manager=proxy_manager, num_threads=num_threads)
            console.print()
            Prompt.ask("  [dim]Pressione Enter para voltar ao menu[/dim]")


if __name__ == "__main__":
    main()
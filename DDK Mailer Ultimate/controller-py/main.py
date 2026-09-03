#!/usr/bin/env python3
"""
DDK Mass Mailer v5.0 — Entry Point (Python + Go Hybrid)
"""
import sys
import os
import signal
import time
import random
import atexit

from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    MAX_WORKERS, SMTP_FILE, PROXY_FILE, EMAIL_LIST_FILE,
    FROM_NAMES_FILE, SUBJECTS_FILE, HTML_TEMPLATE,
    DELAY_BETWEEN_EMAILS, ENABLE_SPINTAX, USE_PROXY,
    ADD_LIST_UNSUBSCRIBE, USE_GO_ENGINE, CONNECTIONS_PER_SMTP,
)
from utils import Display, logger, load_file_lines, validate_email, console
from smtp_manager import SMTPManager
from email_builder import EmailBuilder
from proxy_manager import ProxyManager
from thread_manager import ThreadManager, SendTask
from smtp_checker import run_checker
from go_bridge import GoEngine, GoEngineError

# Engine Go global
GLOBAL_GO_ENGINE = None


def initialize_go_engine():
    """Inicializa Go engine uma única vez"""
    global GLOBAL_GO_ENGINE
    if GLOBAL_GO_ENGINE is not None:
        return GLOBAL_GO_ENGINE

    if not USE_GO_ENGINE:
        Display.warning("Go Engine desativado no config")
        return None

    try:
        engine = GoEngine()
        engine.start()
        GLOBAL_GO_ENGINE = engine
        atexit.register(engine.stop)
        return engine
    except GoEngineError as e:
        Display.warning(f"Go Engine indisponível: {e}")
        Display.info("Compile o motor: cd engine-go && go build -o ddk-engine")
        return None


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

    # Template default RFC-friendly
    if not os.path.exists(HTML_TEMPLATE):
        with open(HTML_TEMPLATE, "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><title>{{brand_name}}</title></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;">
    <h2 style="color:#2c3e50;">Olá!</h2>
    <p>Esta é uma comunicação oficial de {{brand_name}}.</p>
    <p>Data: {{date}}</p>
    <p>Se você não deseja mais receber nossos emails,
       <a href="https://{{from_domain}}/unsubscribe?id={{unsubscribe_id}}">clique aqui para descadastrar</a>.
    </p>
</body>
</html>""")


def load_subjects():
    lines = load_file_lines(SUBJECTS_FILE)
    return lines if lines else ["Comunicado Importante"]


def show_main_menu():
    console.clear()
    title = Text()
    title.append("⚡ DDK MASS MAILER v5.0 ⚡\n", style="bold cyan")
    title.append("Python + Go Hybrid • RFC 8058 • Inbox Optimized", style="dim white")
    console.print(Panel(
        Align.center(title),
        box=box.DOUBLE_EDGE, border_style="bright_blue", padding=(1, 2),
    ))

    menu = """
  [bold cyan][1][/bold cyan] 🚀  [bold white]Iniciar Envio em Massa[/bold white]
  [bold cyan][2][/bold cyan] 🔍  [bold white]Verificar SMTPs (Checker Seguro)[/bold white]
  [bold cyan][3][/bold cyan] 🧹  [bold white]Verificar SMTPs + Limpar Mortos[/bold white]
  [bold cyan][4][/bold cyan] 🔧  [bold white]Status do Go Engine[/bold white]
  [bold cyan][0][/bold cyan] ❌  [bold white]Sair[/bold white]
"""
    console.print(menu)
    return Prompt.ask("  Selecione uma opção", choices=["0", "1", "2", "3", "4"], default="1")


def run_sender(go_engine):
    smtp_lines = load_file_lines(SMTP_FILE)
    if not smtp_lines:
        Display.warning(f"Nenhum SMTP em {SMTP_FILE}")
        return

    email_lines = load_file_lines(EMAIL_LIST_FILE)
    if not email_lines:
        Display.warning(f"Nenhum email em {EMAIL_LIST_FILE}")
        return

    valid_emails = [e for e in email_lines if validate_email(e)]
    invalid_count = len(email_lines) - len(valid_emails)
    if invalid_count > 0:
        Display.warning(f"{invalid_count} emails inválidos removidos")

    proxy_lines = load_file_lines(PROXY_FILE)
    subjects = load_subjects()

    Display.show_inputs_overview(len(smtp_lines), len(proxy_lines), len(valid_emails), len(subjects))

    console.rule("[bold cyan]Configuração[/bold cyan]")

    use_template = Confirm.ask("📄 Usar template HTML?", default=True)
    html_body = None
    if not use_template:
        html_body = Prompt.ask("📝 Cole o HTML do corpo")

    reply_to = Prompt.ask("↩️  Reply-To [dim](Enter = pular)[/dim]", default="")
    num_workers = int(Prompt.ask("🧵 Workers", default=str(MAX_WORKERS)))
    delay = float(Prompt.ask("⏱️  Delay (s)", default=str(DELAY_BETWEEN_EMAILS)))
    use_proxy = Confirm.ask("🔒 Usar proxies?", default=bool(proxy_lines))
    warmup = Confirm.ask("🔥 Modo warmup?", default=False)
    use_attachments = Confirm.ask("📎 Anexos?", default=False)
    spintax = Confirm.ask("🔀 Spintax?", default=ENABLE_SPINTAX)

    import config
    config.DELAY_BETWEEN_EMAILS = delay
    config.USE_PROXY = use_proxy
    config.ENABLE_SPINTAX = spintax

    settings = {
        "use_go": go_engine is not None,
        "subjects_info": f"{len(subjects)} em rotação" if len(subjects) > 1 else subjects[0],
        "num_workers": num_workers,
        "conns_per_smtp": CONNECTIONS_PER_SMTP,
        "delay": delay,
        "use_proxy": use_proxy,
        "warmup": warmup,
        "use_attachments": use_attachments,
        "spintax": spintax,
        "list_unsub": ADD_LIST_UNSUBSCRIBE,
    }

    proxy_manager = None
    if use_proxy:
        proxy_manager = ProxyManager(PROXY_FILE)
        if not proxy_manager.has_proxies():
            Display.warning("Nenhum proxy, enviando direto")
            proxy_manager = None

    smtp_manager = SMTPManager(proxy_manager=proxy_manager)
    smtp_manager.load_accounts()

    if smtp_manager.get_active_count() == 0:
        Display.warning("Nenhum SMTP válido!")
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
        go_engine=go_engine,
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

    try:
        if warmup:
            thread_manager.send_warmup(tasks, num_workers=num_workers)
        else:
            thread_manager.send_batch(tasks, num_workers=num_workers)
    finally:
        # Aplica remoções pendentes de forma ATÔMICA (final da sessão)
        Display.info("Aplicando limpezas pendentes...")
        smtp_manager.flush_removals()

    elapsed = time.time() - start_time
    Display.summary(elapsed)


def show_engine_status(go_engine):
    console.print()
    if go_engine and go_engine.process:
        try:
            resp = go_engine.send_command({"command": "ping"})
            if resp and resp.get("success"):
                console.print(Panel(
                    f"[bold green]✔ Go Engine ATIVO[/bold green]\n"
                    f"Versão: {resp.get('version', '?')}\n"
                    f"PID: {go_engine.process.pid}\n"
                    f"Path: {go_engine.engine_path}",
                    title="🔧 Status do Motor",
                    border_style="green",
                ))
            else:
                console.print("[red]Engine não responde[/red]")
        except Exception as e:
            console.print(f"[red]Erro: {e}[/red]")
    else:
        console.print(Panel(
            "[yellow]Go Engine INATIVO[/yellow]\n\n"
            "Para ativar:\n"
            "  1. cd engine-go\n"
            "  2. go build -o ddk-engine\n"
            "  3. Reinicie o Python",
            title="🔧 Status do Motor",
            border_style="yellow",
        ))
    console.print()


def main():
    create_directories()
    create_default_files()

    Display.banner()
    console.print("\n  [cyan]Inicializando Go Engine...[/cyan]")
    go_engine = initialize_go_engine()

    if go_engine:
        Display.info("[green]Sistema pronto (Go Engine ativo)[/green]")
    else:
        Display.warning("Sistema iniciado sem Go Engine (funcionalidade limitada)")

    time.sleep(1)

    while True:
        choice = show_main_menu()

        if choice == "0":
            console.print("\n  [dim]Encerrando...[/dim]")
            if go_engine:
                go_engine.stop()
            console.print("  [dim]Até logo![/dim]\n")
            sys.exit(0)

        elif choice == "1":
            if not go_engine:
                Display.warning("Go Engine é necessário. Compile em engine-go/")
                Prompt.ask("  [dim]Enter para voltar[/dim]")
                continue
            run_sender(go_engine)
            console.print()
            Prompt.ask("  [dim]Enter para voltar ao menu[/dim]")

        elif choice == "2":
            if not go_engine:
                Display.warning("Go Engine é necessário")
                Prompt.ask("  [dim]Enter para voltar[/dim]")
                continue
            proxy_lines = load_file_lines(PROXY_FILE)
            proxy_manager = None
            if proxy_lines and Confirm.ask("🔒 Usar proxies?", default=True):
                proxy_manager = ProxyManager(PROXY_FILE)
            num_threads = int(Prompt.ask("🧵 Threads", default="10"))
            run_checker(proxy_manager=proxy_manager, num_threads=num_threads, go_engine=go_engine)
            Prompt.ask("  [dim]Enter para voltar[/dim]")

        elif choice == "3":
            if not go_engine:
                Display.warning("Go Engine é necessário")
                Prompt.ask("  [dim]Enter para voltar[/dim]")
                continue
            proxy_lines = load_file_lines(PROXY_FILE)
            proxy_manager = ProxyManager(PROXY_FILE) if proxy_lines else None
            num_threads = int(Prompt.ask("🧵 Threads", default="10"))
            run_checker(proxy_manager=proxy_manager, num_threads=num_threads, go_engine=go_engine)
            Prompt.ask("  [dim]Enter para voltar[/dim]")

        elif choice == "4":
            show_engine_status(go_engine)
            Prompt.ask("  [dim]Enter para voltar[/dim]")


if __name__ == "__main__":
    main()
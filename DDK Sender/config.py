"""
Configurations Globais - DDK v4.0 Anti-Spam com Inteligência de Reputation
Todas as recomendações de otimização aplicadas
"""

# ── Threading ──────────────────────────────────────────
MAX_THREADS = 3                    # 3 threads
DELAY_BETWEEN_EMAILS = 8           # 8 segundos (aumentado de 5)
DELAY_JITTER_MIN = 0.8             # Variação mínima
DELAY_JITTER_MAX = 1.5             # Variação máxima
DELAY_BETWEEN_BATCHES = 15         # 15s pausa entre lotes (AGORA USADO!)
BATCH_SIZE = 20                    # Lote pequeno
WARMUP_PAUSE_MIN = 30              # Pausa mínima entre rounds warmup
WARMUP_PAUSE_MAX = 60              # Pausa máxima (aleatória)

# ── SMTP ───────────────────────────────────────────────
SMTP_TIMEOUT = 15
SMTP_RETRY_ATTEMPTS = 2            # Aumentado de 1
SMTP_RETRY_DELAY = 2               # Aumentado de 1
SMTP_RETRY_BACKOFF = True          # Exponential backoff em retries
USE_TLS = True
USE_SSL = True

# ── Gerenciamento de SMTP ─────────────────────────────
MAX_EMAILS_PER_SMTP = 1            # 1 email por SMTP (força rotação)
MAX_FAILURES_BEFORE_REMOVE = 2     # Aumentado de 1 (menos false positives)
REMOVE_SMTP_ON_FAIL = True
SAVE_REMOVED_SMTPS = True
AUTO_CLEAN_INPUT_FILE = True
REACTIVATION_COOLDOWN = 60         # 60s mínimo entre reciclagens de SMTP
REMOVE_ACCOUNT_MIN_INTERVAL = 30   # 30s entre removals para evitar spike

# ── Ratelimit Inteligente ──────────────────────────────
RATELIMIT_BACKOFF = {
    "429": 600,                    # Too Many Requests = 10min
    "4.7.1": 1800,                 # Service Unavailable = 30min  
    "4.2.1": 3600,                 # Mailbox Unavailable = 1h
    "4.2.2": 7200,                 # Mailbox Full = 2h
    "5.1.1": 86400,                # User not found = 24h (próximo dia)
    "default": 300,                # Fallback = 5min
}

# ── Email Building ─────────────────────────────────────
DEFAULT_CHARSET = "utf-8"
DEFAULT_CONTENT_TYPE = "multipart/alternative"
MAX_SUBJECT_LENGTH = 78
ADD_TEXT_PART = True
ADD_LIST_UNSUBSCRIBE = False
ADD_MESSAGE_ID = True
ADD_DATE_HEADER = True
ADD_MIME_VERSION = True

# ── Headers Dinâmicos (NOVO!) ──────────────────────────
RANDOMIZE_HEADERS = True           # Ativa randomização de headers
RANDOMIZE_MESSAGE_ID = True        # Message-ID com variação
RANDOMIZE_DATE_JITTER = True       # Date com ±45min jitter
RANDOMIZE_DOMAIN_SUFFIX = True     # Domain suffix aleatório no Message-ID

# ── Spintax & Obfuscation ──────────────────────────────
ENABLE_SPINTAX = True              # Parsing de {a|b|c}
ENABLE_HASH_BUSTER = False         # Hash buster (quando habilitado)
ENABLE_HEADER_OBFUSCATION = True   # Ofusca headers adicionais (NOVO!)

# ── Connection Pooling (NOVO!) ─────────────────────────
ENABLE_CONNECTION_POOLING = True   # Reutiliza conexões SMTP
MAX_REUSES_PER_CONNECTION = 5      # Máximo de reusos antes de reconectar
CONNECTION_POOL_TIMEOUT = 300      # 5 min antes de fechar idle pool

# ── Anexos ─────────────────────────────────────────────
ADD_ATTACHMENTS = False
ATTACHMENTS_FOLDER = "attachments"
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024

# ── Proxy ──────────────────────────────────────────────
USE_PROXY = True
PROXY_TYPE = "socks5"
PROXY_ROTATION = True
PROXY_TIMEOUT = 10
PROXY_RETRY_ON_FAIL = True         # Retry com proxy diferente se falhar
PROXY_ERROR_THRESHOLD = 3          # Quantos erros antes de marcar proxy como ruim

# ── DNS & Reputation Check (NOVO!) ────────────────────
CHECK_DNS_BEFORE_SEND = False      # Validar SPF/DKIM/DMARC (pode ser lento)
CHECK_IP_REPUTATION = False        # Validar IP em blacklists
SKIP_INVALID_DNS_DOMAINS = True    # Pula SMTPs com domínio inválido

# ── Paths ──────────────────────────────────────────────
SMTP_FILE = "inputs/smtps.txt"
PROXY_FILE = "inputs/proxies.txt"
EMAIL_LIST_FILE = "inputs/emails.txt"
FROM_NAMES_FILE = "inputs/from_names.txt"
SUBJECTS_FILE = "inputs/subjects.txt"
SUBJECT_FILE = "inputs/subjects.txt"
HTML_TEMPLATE = "templates/default.html"
LOG_DIR = "logs"
REMOVED_SMTP_FILE = "logs/removed_smtps.txt"
PROXY_FAILED_FILE = "logs/failed_proxies.txt"

# ── Warmup ─────────────────────────────────────────────
WARMUP_MODE = False
WARMUP_START = 5
WARMUP_INCREMENT = 5
WARMUP_PAUSE = 10

# ── Logging ────────────────────────────────────────────
LOG_LEVEL = "DEBUG"                # DEBUG, INFO, WARNING, ERROR
LOG_TO_CONSOLE = True
LOG_TO_FILE = True
LOG_INCLUDE_IP = True              # Log mostra IP/Proxy usado
LOG_INCLUDE_TIMING = True          # Log mostra tempo de conexão

# ── Estatísticas (NOVO!) ───────────────────────────────
ENABLE_STATS = True                # Coleta estatísticas
STATS_INTERVAL = 30                # Mostra stats a cada 30 emails
STATS_FILE = "logs/stats.json"     # Exporta stats em JSON

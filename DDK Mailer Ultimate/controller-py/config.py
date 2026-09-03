"""
Configurações Globais - DDK v5.0 (Híbrido Python + Go)
"""
import os

# ── Engine Go ──────────────────────────────────────────
GO_ENGINE_PATH = os.path.join(os.path.dirname(__file__), "..", "engine-go", "ddk-engine")
USE_GO_ENGINE = True  # False para usar fallback Python puro
GO_ENGINE_TIMEOUT = 60

# ── Threading / Concorrência ───────────────────────────
MAX_WORKERS = 10
CONNECTIONS_PER_SMTP = 3  # Conexões simultâneas por conta SMTP
EMAILS_PER_CONNECTION = 20  # Emails enviados por conexão antes de reciclar
DELAY_BETWEEN_EMAILS = 2.0
DELAY_JITTER_MIN = 0.5
DELAY_JITTER_MAX = 1.5
DELAY_BETWEEN_BATCHES = 10
BATCH_SIZE = 50

# ── SMTP ───────────────────────────────────────────────
SMTP_TIMEOUT = 20
SMTP_RETRY_ATTEMPTS = 3  # Corrigido: mínimo 3 para lidar com erros 4xx
SMTP_RETRY_DELAY = 2

# ── Gerenciamento de SMTP ─────────────────────────────
MAX_EMAILS_PER_SMTP = 100  # Aumentado (evita underuse)
MAX_FAILURES_BEFORE_REMOVE = 3
REMOVE_SMTP_ON_FAIL = True
SAVE_REMOVED_SMTPS = True
AUTO_CLEAN_INPUT_FILE = False  # DESATIVADO por segurança (agora só ao final)
REACTIVATION_INTERVAL = 300

# ── Rate limit Dinâmico ────────────────────────────────
RATELIMIT_BACKOFF = {
    "429": 600,
    "4.7.1": 1800,
    "4.2.1": 86400,
    "default": 300,
}

# ── Email Building ─────────────────────────────────────
DEFAULT_CHARSET = "utf-8"
ADD_TEXT_PART = True
ADD_LIST_UNSUBSCRIBE = True  # CORRIGIDO: obrigatório Google/Yahoo 2024
LIST_UNSUBSCRIBE_URL = "https://{domain}/unsubscribe?id={id}"
LIST_UNSUBSCRIBE_MAILTO = "unsubscribe@{domain}"
ADD_MESSAGE_ID = True
ADD_DATE_HEADER = True
ADD_MIME_VERSION = True

# ── Anti-Spam Modernas ────────────────────────────────
ENABLE_SPINTAX = True
ENABLE_TEXT_VARIATION = True  # Variação real em texto visível (substitui hash buster)
USE_NATIVE_BASE64_MIME = True  # Base64 nativo do MIME (não JavaScript!)
DISABLE_LEGACY_HASH_BUSTER = True  # Desabilita CSS oculto obsoleto

# ── Spoofing ────────────────────────────────────────────
SPOOF_NAMES = True
SPOOF_NAMES_FILE = "inputs/spoof_names.txt"

# ── Anexos ─────────────────────────────────────────────
ADD_ATTACHMENTS = False
ATTACHMENTS_FOLDER = "attachments"
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024

# ── Proxy ──────────────────────────────────────────────
USE_PROXY = True
PROXY_TYPE = "socks5"
PROXY_ROTATION = True
PROXY_TIMEOUT = 10

# ── TLS/SSL ────────────────────────────────────────────
TLS_MIN_VERSION = "TLSv1.2"
TLS_VERIFY_CERT = True  # CORRIGIDO: validação SSL ativa

# ── Paths ──────────────────────────────────────────────
SMTP_FILE = "inputs/smtps.txt"
PROXY_FILE = "inputs/proxies.txt"
EMAIL_LIST_FILE = "inputs/emails.txt"
FROM_NAMES_FILE = "inputs/from_names.txt"
SUBJECTS_FILE = "inputs/subjects.txt"
HTML_TEMPLATE = "templates/default.html"
LOG_DIR = "logs"
REMOVED_SMTP_FILE = "logs/removed_smtps.txt"

# ── Warmup ─────────────────────────────────────────────
WARMUP_MODE = False
WARMUP_START = 5
WARMUP_INCREMENT = 5
WARMUP_PAUSE = 60

# ── Segurança ──────────────────────────────────────────
MASK_CREDENTIALS_IN_LOGS = True  # Máscara senha em logs
SECURE_FILE_WRITE = True  # Escrita atômica via tempfile
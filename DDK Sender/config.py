"""
Configurações Globais - DDK v3.7 Anti-Spam
"""

# ── Threading ──────────────────────────────────────────
MAX_THREADS = 3                    # 3 threads
DELAY_BETWEEN_EMAILS = 5           # 5 segundos
DELAY_JITTER_MIN = 0.8             # Variação mínima
DELAY_JITTER_MAX = 1.5             # Variação máxima
DELAY_BETWEEN_BATCHES = 10         # Pausa entre lotes
BATCH_SIZE = 20                    # Lote pequeno

# ── SMTP ───────────────────────────────────────────────
SMTP_TIMEOUT = 15
SMTP_RETRY_ATTEMPTS = 1
SMTP_RETRY_DELAY = 1
USE_TLS = True
USE_SSL = True

# ── Gerenciamento de SMTP ─────────────────────────────
MAX_EMAILS_PER_SMTP = 1            # 2 emails por SMTP
MAX_FAILURES_BEFORE_REMOVE = 1
REMOVE_SMTP_ON_FAIL = True
SAVE_REMOVED_SMTPS = True
AUTO_CLEAN_INPUT_FILE = True

# ── Email Building ─────────────────────────────────────
DEFAULT_CHARSET = "utf-8"
DEFAULT_CONTENT_TYPE = "multipart/alternative"
MAX_SUBJECT_LENGTH = 78
ADD_TEXT_PART = True
ADD_LIST_UNSUBSCRIBE = False
ADD_MESSAGE_ID = True
ADD_DATE_HEADER = True
ADD_MIME_VERSION = True
RANDOMIZE_HEADERS = False
ENABLE_SPINTAX = True              # ← FALTANDO
ENABLE_HASH_BUSTER = False          # ← FALTANDO

# ── Anexos ─────────────────────────────────────────────
ADD_ATTACHMENTS = False
ATTACHMENTS_FOLDER = "attachments"
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024

# ── Proxy ──────────────────────────────────────────────
USE_PROXY = True
PROXY_TYPE = "socks5"
PROXY_ROTATION = True
PROXY_TIMEOUT = 10

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

# ── Warmup ─────────────────────────────────────────────
WARMUP_MODE = False
WARMUP_START = 5
WARMUP_INCREMENT = 5
WARMUP_PAUSE = 10
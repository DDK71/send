"""
Construtor de Emails - DDK v5.0 (RFC 8058 Compliant + Anti-Spam Moderno)
"""
import random
import string
import time
import re
import os
import html as html_module
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.header import Header
from email.utils import formatdate, make_msgid, formataddr
from email import encoders

import config
from utils import random_string, extract_domain, sanitize_html, logger, load_file_lines


class EmailBuilder:
    NATURAL_VARIATIONS = [
        "Obrigado pela sua atenção.",
        "Agradecemos o seu tempo.",
        "Atenciosamente,",
        "Cordialmente,",
        "Com os melhores cumprimentos,",
        "Aguardamos seu retorno.",
        "Fico à disposição para esclarecimentos.",
        "Qualquer dúvida, estamos à disposição.",
    ]

    def __init__(self):
        self.html_template = None
        self.from_names = []
        self.spoof_names = []
        self.attachments = []
        self.use_attachments = config.ADD_ATTACHMENTS
        self._load_spoof_names()

    def _load_spoof_names(self):
        if config.SPOOF_NAMES:
            try:
                self.spoof_names = load_file_lines(config.SPOOF_NAMES_FILE)
                if self.spoof_names:
                    logger.info(f"{len(self.spoof_names)} nomes de spoofing carregados")
            except Exception:
                self.spoof_names = []

    def load_template(self, template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                self.html_template = f.read()
            logger.info(f"Template carregado: {template_path}")
        except Exception:
            self.html_template = "<html><body><p>{{message}}</p></body></html>"

    def load_from_names(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                self.from_names = [line.strip() for line in f if line.strip()]
        except Exception:
            self.from_names = []

    def enable_attachments(self, enabled=True):
        self.use_attachments = enabled
        if enabled:
            self._load_attachments()

    def _load_attachments(self):
        if not os.path.exists(config.ATTACHMENTS_FOLDER):
            os.makedirs(config.ATTACHMENTS_FOLDER, exist_ok=True)
            return
        self.attachments = []
        for filename in os.listdir(config.ATTACHMENTS_FOLDER):
            filepath = os.path.join(config.ATTACHMENTS_FOLDER, filename)
            if os.path.isfile(filepath):
                if os.path.getsize(filepath) <= config.MAX_ATTACHMENT_SIZE:
                    self.attachments.append(filepath)

    @staticmethod
    def extract_brand_name(from_email):
        domain = extract_domain(from_email)
        main_part = domain.split(".")[0]
        brand = re.sub(r"[-_]+", " ", main_part).strip().title()
        return brand if brand else "Central"

    def _apply_natural_variation(self, html_content):
        if not config.ENABLE_TEXT_VARIATION:
            return html_content

        variation = random.choice(self.NATURAL_VARIATIONS)
        signature_id = random_string(6).upper()

        natural_footer = (
            f'\n<p style="font-size:12px;color:#666666;margin-top:20px;">'
            f'{variation}<br><span style="color:#999999;">Ref: {signature_id}</span>'
            f'</p>\n'
        )
        return html_content + natural_footer

    def _get_from_name(self, from_email, template_vars):
        if config.SPOOF_NAMES and self.spoof_names:
            chosen = random.choice(self.spoof_names)
            if config.ENABLE_SPINTAX:
                chosen = self._parse_spintax(chosen)
            chosen = self._apply_variables(chosen, template_vars)
            return chosen

        email_username = from_email.split("@")[0] if "@" in from_email else from_email

        if self.from_names:
            chosen = random.choice(self.from_names)
            if config.ENABLE_SPINTAX:
                chosen = self._parse_spintax(chosen)
            chosen = self._apply_variables(chosen, template_vars)
            chosen = chosen.replace("{username}", email_username)
            return chosen

        return email_username

    def _parse_spintax(self, text):
        if not text:
            return ""
        max_iterations = 10
        pattern = re.compile(r"\{([^{}|]+(?:\|[^{}|]+)+)\}")
        for _ in range(max_iterations):
            match = pattern.search(text)
            if not match:
                break
            options = match.group(1).split("|")
            chosen = random.choice(options).strip()
            text = text[: match.start()] + chosen + text[match.end():]
        return text

    def _apply_variables(self, content, variables):
        if not content or not variables:
            return content
        for key, value in variables.items():
            val = str(value)
            content = content.replace(f"{{{{{key}}}}}", val)
            content = content.replace(f"{{{key}}}", val)
        return content

    def _html_to_text(self, html_content):
        text = html_content
        text = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r"\2 (\1)", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "  - ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_module.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _attach_file(self, msg, filepath):
        filename = os.path.basename(filepath)
        try:
            ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
            if ext in ("png", "jpg", "jpeg", "gif", "bmp"):
                with open(filepath, "rb") as f:
                    part = MIMEImage(f.read())
            elif ext == "pdf":
                with open(filepath, "rb") as f:
                    part = MIMEApplication(f.read(), _subtype="pdf")
            else:
                ctype, encoding = mimetypes.guess_type(filepath)
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                with open(filepath, "rb") as f:
                    part = MIMEBase(maintype, subtype)
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
            logger.info(f"Anexado: {filename}")
        except Exception as e:
            logger.error(f"Erro ao anexar {filename}: {e}")

    def build(self, from_email, to_email, subject, html_body=None, text_body=None,
              reply_to=None, custom_headers=None, template_vars=None):
        template_vars = template_vars or {}

        to_email = self._sanitize_header_value(to_email)
        from_email = self._sanitize_header_value(from_email)
        subject = self._sanitize_header_value(subject)

        if html_body is None and self.html_template:
            html_body = self.html_template

        subject = self._apply_variables(subject, template_vars)
        if html_body:
            html_body = self._apply_variables(html_body, template_vars)

        if config.ENABLE_SPINTAX:
            subject = self._parse_spintax(subject)
            if html_body:
                html_body = self._parse_spintax(html_body)

        if html_body:
            html_body = sanitize_html(html_body)
            html_body = self._apply_natural_variation(html_body)

        if text_body is None and html_body and config.ADD_TEXT_PART:
            text_body = self._html_to_text(html_body)

        if self.use_attachments and self.attachments:
            msg = MIMEMultipart("mixed")
        else:
            msg = MIMEMultipart("alternative")

        from_domain = extract_domain(from_email)
        from_name = self._get_from_name(from_email, template_vars)

        if from_name:
            msg["From"] = formataddr((str(Header(from_name, "utf-8")), from_email))
        else:
            msg["From"] = from_email

        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")

        if config.ADD_MESSAGE_ID:
            random_suffix = random_string(8)
            msg["Message-ID"] = f"<{int(time.time()*1000)}.{random_suffix}@{from_domain}>"

        if config.ADD_DATE_HEADER:
            jitter = random.randint(-1800, 1800)
            msg["Date"] = formatdate(time.time() + jitter, localtime=True)

        if config.ADD_MIME_VERSION:
            msg["MIME-Version"] = "1.0"

        if reply_to:
            msg["Reply-To"] = self._sanitize_header_value(reply_to)

        if config.ADD_LIST_UNSUBSCRIBE:
            unsub_id = random_string(16)
            unsub_url = config.LIST_UNSUBSCRIBE_URL.format(domain=from_domain, id=unsub_id)
            unsub_mailto = config.LIST_UNSUBSCRIBE_MAILTO.format(domain=from_domain)
            msg["List-Unsubscribe"] = f"<{unsub_url}>, <mailto:{unsub_mailto}?subject=unsubscribe-{unsub_id}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            msg["List-Id"] = f"<newsletter.{from_domain}>"

        if custom_headers:
            for k, v in custom_headers.items():
                msg[k] = self._sanitize_header_value(v)

        if self.use_attachments and self.attachments:
            body_container = MIMEMultipart("alternative")
            if text_body:
                body_container.attach(MIMEText(text_body, "plain", _charset="utf-8"))
            if html_body:
                body_container.attach(MIMEText(html_body, "html", _charset="utf-8"))
            msg.attach(body_container)
            for filepath in self.attachments:
                self._attach_file(msg, filepath)
        else:
            if text_body:
                msg.attach(MIMEText(text_body, "plain", _charset="utf-8"))
            if html_body:
                msg.attach(MIMEText(html_body, "html", _charset="utf-8"))

        return msg

    @staticmethod
    def _sanitize_header_value(value):
        if not value:
            return value
        return str(value).replace("\r", "").replace("\n", "").strip()

    def build_raw(self, from_email, to_email, subject, html_body=None, text_body=None,
                  reply_to=None, custom_headers=None, template_vars=None):
        msg = self.build(
            from_email=from_email, to_email=to_email, subject=subject,
            html_body=html_body, text_body=text_body,
            reply_to=reply_to, custom_headers=custom_headers,
            template_vars=template_vars,
        )
        return msg.as_string()
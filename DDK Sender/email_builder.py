"""
Construtor de Emails — DDK v3.8 (Sem Headers Extras - Idêntico ao Checker)
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
from email.utils import formatdate, make_msgid
from email import encoders

from config import (
    DEFAULT_CHARSET,
    ADD_TEXT_PART,
    ADD_LIST_UNSUBSCRIBE,
    RANDOMIZE_HEADERS,
    ADD_MESSAGE_ID,
    ADD_DATE_HEADER,
    ADD_MIME_VERSION,
    ADD_ATTACHMENTS,
    ATTACHMENTS_FOLDER,
    MAX_ATTACHMENT_SIZE,
    ENABLE_SPINTAX,
)
from utils import (
    random_string,
    extract_domain,
    sanitize_html,
    logger,
)


class EmailBuilder:
    def __init__(self):
        self.html_template = None
        self.from_names = []
        self.attachments = []
        self.use_attachments = ADD_ATTACHMENTS

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
        if not os.path.exists(ATTACHMENTS_FOLDER):
            os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)
            return

        self.attachments = []
        for filename in os.listdir(ATTACHMENTS_FOLDER):
            filepath = os.path.join(ATTACHMENTS_FOLDER, filename)
            if os.path.isfile(filepath):
                size = os.path.getsize(filepath)
                if size <= MAX_ATTACHMENT_SIZE:
                    self.attachments.append(filepath)
                    logger.info(f"Anexo: {filename} ({size / 1024:.1f} KB)")

    @staticmethod
    def extract_brand_name(from_email):
        domain = extract_domain(from_email)
        main_part = domain.split(".")[0]
        brand = re.sub(r"[-_]+", " ", main_part).strip().title()
        return brand if brand else "Central"

    def _get_from_name(self, from_email, template_vars):
        email_username = from_email.split("@")[0] if "@" in from_email else from_email
        email_username = email_username.strip()
        
        if self.from_names:
            chosen = random.choice(self.from_names)
            if ENABLE_SPINTAX:
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
            text = text[: match.start()] + chosen + text[match.end() :]
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
                    part.add_header("Content-Disposition", "attachment", filename=filename)
                    msg.attach(part)
            elif ext == "pdf":
                with open(filepath, "rb") as f:
                    part = MIMEApplication(f.read(), _subtype="pdf")
                    part.add_header("Content-Disposition", "attachment", filename=filename)
                    msg.attach(part)
            elif ext in ("zip", "rar", "7z"):
                mime_type = "application/zip" if ext == "zip" else "application/x-rar-compressed"
                with open(filepath, "rb") as f:
                    part = MIMEBase("application", ext)
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Type", f'{mime_type}; name="{filename}"')
                    part.add_header("Content-Disposition", "attachment", filename=filename)
                    msg.attach(part)
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

    def build(
        self,
        from_email,
        to_email,
        subject,
        html_body=None,
        text_body=None,
        reply_to=None,
        custom_headers=None,
        template_vars=None,
    ):
        template_vars = template_vars or {}

        # 1. Conteúdos
        if html_body is None and self.html_template:
            html_body = self.html_template

        subject = self._apply_variables(subject, template_vars)
        if html_body:
            html_body = self._apply_variables(html_body, template_vars)

        if ENABLE_SPINTAX:
            subject = self._parse_spintax(subject)
            if html_body:
                html_body = self._parse_spintax(html_body)

        if html_body:
            html_body = sanitize_html(html_body)

        if text_body is None and html_body and ADD_TEXT_PART:
            text_body = self._html_to_text(html_body)

        # 2. Container MIME
        if self.use_attachments and self.attachments:
            msg = MIMEMultipart("mixed")
        else:
            msg = MIMEMultipart("alternative")

        # 3. Headers PRINCIPAIS (sem extras)
        from_domain = extract_domain(from_email)
        from_name = self._get_from_name(from_email, template_vars)

        if from_name:
            msg["From"] = f"{Header(from_name, 'utf-8').encode()} <{from_email}>"
        else:
            msg["From"] = from_email

        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")

        # 4. Headers BÁSICOS (apenas essenciais)
        if ADD_MESSAGE_ID:
            msg["Message-ID"] = make_msgid(domain=from_domain if "." in from_domain else "local")
        if ADD_DATE_HEADER:
            msg["Date"] = formatdate(localtime=True)
        if ADD_MIME_VERSION:
            msg["MIME-Version"] = "1.0"
        if reply_to:
            msg["Reply-To"] = reply_to

        # 5. List-Unsubscribe (opcional, igual ao Checker)
        if ADD_LIST_UNSUBSCRIBE:
            unsub_id = random_string(12)
            msg["List-Unsubscribe"] = f"<mailto:unsubscribe-{unsub_id}@{from_domain}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        # 6. Headers customizados (se houver)
        if custom_headers:
            for k, v in custom_headers.items():
                msg[k] = v

        # 7. REMOVIDOS: X-Mailer, X-Priority, Importance
        # Estes headers NÃO são adicionados (igual ao Checker)

        # 8. Conteúdo
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

    def build_raw(
        self,
        from_email,
        to_email,
        subject,
        html_body=None,
        text_body=None,
        reply_to=None,
        custom_headers=None,
        template_vars=None,
    ):
        msg = self.build(
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            reply_to=reply_to,
            custom_headers=custom_headers,
            template_vars=template_vars,
        )
        return msg.as_string()
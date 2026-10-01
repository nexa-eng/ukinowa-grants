"""週報のメール送信。宛先・送信設定は環境変数（GitHub Secrets）から読む。設定がなければ何もしない。

必要な環境変数:
  SMTP_HOST, SMTP_PORT（465 なら SSL、587 なら STARTTLS）, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO（カンマ区切り）
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_report_mail(subject: str, text: str, html_body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("MAIL_TO")
    if not host or not to:
        print("メール設定がないため送信をスキップ")
        return False
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("MAIL_FROM", user)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(a.strip() for a in to.split(",") if a.strip())
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)
    print(f"メール送信: {msg['To']}")
    return True

"""把验证码发到邮箱。log 模式只写服务端日志，便于开发。"""

import smtplib
from email.message import EmailMessage

from src.config.settings import settings


def deliver_login_code(email: str, purpose: str, code: str) -> None:
    """发送一次性验证码。purpose 为 register 或 reset。"""

    mode = settings.auth_email_mode.strip().lower() or "smtp"
    if mode == "log":
        print(f"[auth] {purpose} code for {email}: {code}")
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = email
    message["Subject"] = "引力 YINLI 验证码"
    action = "完成注册" if purpose == "register" else "重置密码"
    message.set_content(f"验证码是 {code}，15 分钟内有效，用于{action}。")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
        if settings.smtp_starttls:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)

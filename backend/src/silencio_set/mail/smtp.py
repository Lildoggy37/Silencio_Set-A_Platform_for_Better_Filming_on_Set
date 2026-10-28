from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
import smtplib
import ssl
from typing import Literal
from uuid import UUID

from silencio_set.core.config import Settings
from silencio_set.mail.payloads import VerificationMail
from silencio_set.mail.invitation_payloads import InvitationMail


@dataclass(frozen=True)
class Delivery:
    status: Literal["accepted", "rejected", "unknown", "not_sent"]
    category: str
    retryable: bool = False

    def __post_init__(self):
        categories = {"SMTP_ACCEPTED", "SMTP_RECIPIENT_REJECTED", "SMTP_REJECTED", "SMTP_FEATURE_UNSUPPORTED",
                      "MAIL_EXPIRED", "DELIVERY_UNKNOWN", "SMTP_CONNECT_FAILED", "PAYLOAD_UNAVAILABLE", "VERIFICATION_SUPERSEDED", "INVITATION_UNAVAILABLE"}
        if self.status not in {"accepted", "rejected", "unknown", "not_sent"} or self.category not in categories:
            raise ValueError("Unsupported delivery classification")
        if self.retryable and self.status != "rejected":
            raise ValueError("Only definitely rejected delivery may be retried")


class SmtpAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, job_id: UUID, payload: VerificationMail | InvitationMail) -> Delivery:
        if payload.expires_at <= datetime.now(UTC).timestamp():
            return Delivery("not_sent", "MAIL_EXPIRED")
        message = EmailMessage()
        message["From"] = self.settings.mail_from
        message["To"] = payload.email
        message["Subject"] = "Silencio Set 剧组邀请" if isinstance(payload, InvitationMail) else "Silencio Set 注册验证码"
        message["Message-ID"] = f"<{job_id}@silencio-set.local>"
        message["Date"] = format_datetime(datetime.now(UTC))
        if isinstance(payload, InvitationMail):
            link = f"{self.settings.invitation_public_origin}/#invitation={payload.token}"
            message.set_content(f"你收到了一份来自「{payload.crew_name}」的剧组邀请。\n\n"
                f"请使用收到邮件的邮箱注册或登录，补全昵称后打开链接并明确确认加入：\n{link}\n\n"
                "邀请自创建起7天有效，可能被管理员撤销；链接不得转发。加入不会自动授予岗位。若非预期邀请，请忽略本邮件。\n")
        else:
            message.set_content(f"你的 Silencio Set 注册验证码：{payload.code}\n\n请在有效期内使用。若非本人操作，请忽略本邮件。\n")
        smtp = None
        submitting = False
        try:
            cls = smtplib.SMTP_SSL if self.settings.mail_mode == "ssl" else smtplib.SMTP
            options = {"timeout": self.settings.mail_timeout}
            if self.settings.mail_mode == "ssl":
                options["context"] = ssl.create_default_context()
            smtp = cls(self.settings.mail_host, self.settings.mail_port, **options)
            smtp.ehlo()
            if self.settings.mail_mode == "starttls":
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if self.settings.mail_username is not None:
                smtp.login(self.settings.mail_username.get_secret_value(), self.settings.mail_password.get_secret_value())
            if payload.expires_at <= datetime.now(UTC).timestamp():
                return Delivery("not_sent", "MAIL_EXPIRED")
            submitting = True
            refused = smtp.send_message(message)
            if refused:
                return Delivery("rejected", "SMTP_RECIPIENT_REJECTED")
            return Delivery("accepted", "SMTP_ACCEPTED")
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [int(result[0]) for result in exc.recipients.values()]
            return Delivery("rejected", "SMTP_RECIPIENT_REJECTED", bool(codes) and all(400 <= c < 500 for c in codes))
        except smtplib.SMTPResponseException as exc:
            return Delivery("rejected", "SMTP_REJECTED", 400 <= exc.smtp_code < 500)
        except smtplib.SMTPNotSupportedError:
            return Delivery("rejected", "SMTP_FEATURE_UNSUPPORTED")
        except (OSError, smtplib.SMTPException):
            return Delivery("unknown", "DELIVERY_UNKNOWN") if submitting else Delivery("rejected", "SMTP_CONNECT_FAILED", True)
        finally:
            if smtp is not None:
                # A failed QUIT must not undo a known positive DATA response.
                try:
                    smtp.close()
                except OSError:
                    pass

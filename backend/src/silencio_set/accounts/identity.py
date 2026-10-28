from dataclasses import dataclass, field
import unicodedata

from email_validator import EmailNotValidError, validate_email


@dataclass(frozen=True)
class EmailIdentity:
    display: str = field(repr=False)
    normalized: str = field(repr=False)


def normalize_email(value: str) -> EmailIdentity:
    if not isinstance(value, str):
        raise ValueError("邮箱必须为字符串")
    display = value.strip()
    if len(display) > 254:
        raise ValueError("邮箱不可超过254字符")
    try:
        # Syntax/IDNA normalization only. Do not perform DNS or SMTP calls here.
        validated = validate_email(display, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("邮箱格式不正确") from None
    normalized = validated.normalized.lower()
    if len(normalized) > 254:
        raise ValueError("规范化邮箱不可超过254字符")
    # Do not strip provider-specific dots or plus suffixes.
    return EmailIdentity(display=display, normalized=normalized)


def normalize_display_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("昵称必须为字符串")
    name = value.strip()
    if not 1 <= len(name) <= 30:
        raise ValueError("昵称去除首尾空白后须为1至30字符")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in name):
        raise ValueError("昵称不能包含控制字符或非法Unicode字符")
    return name

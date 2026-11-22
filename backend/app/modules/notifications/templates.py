"""E-mail templates (plain text, vi/en). Rendering never touches business data directly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Template


@dataclass(frozen=True, slots=True)
class EmailTemplate:
    subject: dict[str, str]
    body: dict[str, str]


FOOTER = {
    "vi": "\n\n—\nVieWallet là ví điện tử MÔ PHỎNG. Không có tiền thật.\n"
    "Nếu bạn không thực hiện thao tác này, hãy đổi mật khẩu ngay.",
    "en": "\n\n—\nVieWallet is a SIMULATED e-wallet. No real money is involved.\n"
    "If you did not do this, change your password now.",
}

TEMPLATES: dict[str, EmailTemplate] = {
    "verify_email": EmailTemplate(
        subject={
            "vi": "Mã xác minh email VieWallet: $code",
            "en": "Your VieWallet verification code: $code",
        },
        body={
            "vi": "Xin chào $name,\n\nMã xác minh email của bạn là: $code\nMã có hiệu lực trong $minutes phút.",
            "en": "Hello $name,\n\nYour e-mail verification code is: $code\nIt expires in $minutes minutes.",
        },
    ),
    "account_exists": EmailTemplate(
        subject={
            "vi": "Có người thử đăng ký bằng email của bạn",
            "en": "Someone tried to sign up with your e-mail",
        },
        body={
            "vi": "Xin chào,\n\nEmail này đã có tài khoản VieWallet. Nếu bạn quên mật khẩu, hãy dùng chức năng Quên mật khẩu.",
            "en": "Hello,\n\nThis e-mail already has a VieWallet account. If you forgot your password, use Forgot password.",
        },
    ),
    "password_reset": EmailTemplate(
        subject={"vi": "Đặt lại mật khẩu VieWallet", "en": "Reset your VieWallet password"},
        body={
            "vi": "Xin chào $name,\n\nMở liên kết sau để đặt mật khẩu mới (hiệu lực $minutes phút, dùng một lần):\n$link",
            "en": "Hello $name,\n\nOpen this link to choose a new password (valid for $minutes minutes, single use):\n$link",
        },
    ),
    "password_changed": EmailTemplate(
        subject={
            "vi": "Mật khẩu VieWallet đã được thay đổi",
            "en": "Your VieWallet password was changed",
        },
        body={
            "vi": "Xin chào $name,\n\nMật khẩu tài khoản của bạn vừa được thay đổi. Các phiên đăng nhập khác đã bị đăng xuất.",
            "en": "Hello $name,\n\nYour account password was just changed. Other sessions were signed out.",
        },
    ),
    "account_locked": EmailTemplate(
        subject={
            "vi": "Tài khoản VieWallet bị tạm khóa",
            "en": "Your VieWallet account is temporarily locked",
        },
        body={
            "vi": "Xin chào $name,\n\nCó quá nhiều lần đăng nhập sai. Tài khoản bị khóa đến $until (giờ Việt Nam).",
            "en": "Hello $name,\n\nToo many failed sign-in attempts. The account is locked until $until (Vietnam time).",
        },
    ),
    "pin_locked": EmailTemplate(
        subject={"vi": "PIN giao dịch bị khóa", "en": "Your transaction PIN is locked"},
        body={
            "vi": "Xin chào $name,\n\nPIN giao dịch bị nhập sai nhiều lần và đã bị khóa. $detail",
            "en": "Hello $name,\n\nYour transaction PIN was entered incorrectly too many times and is locked. $detail",
        },
    ),
    "pin_reset_code": EmailTemplate(
        subject={
            "vi": "Mã đặt lại PIN VieWallet: $code",
            "en": "Your VieWallet PIN reset code: $code",
        },
        body={
            "vi": "Xin chào $name,\n\nMã đặt lại PIN giao dịch: $code (hiệu lực $minutes phút).",
            "en": "Hello $name,\n\nYour transaction PIN reset code: $code (valid for $minutes minutes).",
        },
    ),
    "pin_changed": EmailTemplate(
        subject={"vi": "PIN giao dịch đã được thay đổi", "en": "Your transaction PIN was changed"},
        body={
            "vi": "Xin chào $name,\n\nPIN giao dịch của bạn vừa được đặt hoặc thay đổi.",
            "en": "Hello $name,\n\nYour transaction PIN was just set or changed.",
        },
    ),
    "email_change_code": EmailTemplate(
        subject={"vi": "Xác nhận email mới: $code", "en": "Confirm your new e-mail: $code"},
        body={
            "vi": "Xin chào $name,\n\nMã xác nhận đổi email VieWallet: $code (hiệu lực $minutes phút).",
            "en": "Hello $name,\n\nYour VieWallet e-mail change code: $code (valid for $minutes minutes).",
        },
    ),
    "email_changed": EmailTemplate(
        subject={
            "vi": "Email tài khoản VieWallet đã thay đổi",
            "en": "Your VieWallet e-mail was changed",
        },
        body={
            "vi": "Xin chào $name,\n\nEmail đăng nhập của tài khoản đã đổi sang $new_email.",
            "en": "Hello $name,\n\nThe sign-in e-mail of your account was changed to $new_email.",
        },
    ),
    "mfa_enabled": EmailTemplate(
        subject={"vi": "Đã bật xác thực hai lớp", "en": "Two-factor authentication enabled"},
        body={
            "vi": "Xin chào $name,\n\nXác thực hai lớp (TOTP) đã được bật cho tài khoản nhân viên của bạn.",
            "en": "Hello $name,\n\nTwo-factor authentication (TOTP) was enabled for your staff account.",
        },
    ),
}


class UnknownTemplateError(KeyError):
    pass


def render(template: str, locale: str, params: Mapping[str, str]) -> tuple[str, str]:
    """Return (subject, body). Missing parameters raise KeyError (fail loudly, then retry)."""
    try:
        tpl = TEMPLATES[template]
    except KeyError as exc:
        raise UnknownTemplateError(template) from exc
    lang = locale if locale in tpl.subject else "vi"
    subject = Template(tpl.subject[lang]).substitute(params)
    body = Template(tpl.body[lang]).substitute(params) + FOOTER[lang]
    return subject, body

"""Identity error codes (stable, shared with the frontend)."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.errors import AppError, ConflictError, FieldError, register_titles

register_titles(
    {
        "INVALID_CREDENTIALS": {
            "vi": "Email hoặc mật khẩu không đúng",
            "en": "Incorrect e-mail or password",
        },
        "ACCOUNT_LOCKED": {
            "vi": "Tài khoản tạm thời bị khóa do đăng nhập sai nhiều lần",
            "en": "Account temporarily locked after too many failed sign-ins",
        },
        "ACCOUNT_DISABLED": {"vi": "Tài khoản không hoạt động", "en": "Account is not active"},
        "EMAIL_NOT_VERIFIED": {"vi": "Email chưa được xác minh", "en": "E-mail is not verified"},
        "SESSION_EXPIRED": {"vi": "Phiên đăng nhập đã hết hạn", "en": "Your session has expired"},
        "PASSWORD_POLICY": {
            "vi": "Mật khẩu chưa đủ an toàn",
            "en": "Password does not meet the policy",
        },
        "PASSWORD_INCORRECT": {
            "vi": "Mật khẩu hiện tại không đúng",
            "en": "Current password is incorrect",
        },
        "VERIFICATION_CODE_INVALID": {
            "vi": "Mã xác minh không đúng hoặc đã hết hạn",
            "en": "The code is incorrect or has expired",
        },
        "TOKEN_INVALID": {
            "vi": "Liên kết không hợp lệ hoặc đã hết hạn",
            "en": "The link is invalid or has expired",
        },
        "PIN_NOT_SET": {"vi": "Bạn chưa đặt PIN giao dịch", "en": "Transaction PIN is not set"},
        "PIN_ALREADY_SET": {
            "vi": "PIN giao dịch đã được đặt",
            "en": "Transaction PIN is already set",
        },
        "PIN_INVALID": {"vi": "PIN giao dịch không đúng", "en": "Incorrect transaction PIN"},
        "PIN_LOCKED": {"vi": "PIN giao dịch đang bị khóa", "en": "Transaction PIN is locked"},
        "PIN_WEAK": {"vi": "PIN quá dễ đoán", "en": "PIN is too easy to guess"},
        "MFA_REQUIRED": {"vi": "Cần xác thực hai lớp", "en": "Two-factor authentication required"},
        "MFA_ENROLLMENT_REQUIRED": {
            "vi": "Cần thiết lập xác thực hai lớp",
            "en": "Two-factor authentication must be set up",
        },
        "MFA_INVALID": {"vi": "Mã xác thực không đúng", "en": "Incorrect authentication code"},
        "MFA_STEP_UP_REQUIRED": {
            "vi": "Vui lòng xác thực lại để tiếp tục",
            "en": "Please re-authenticate to continue",
        },
        "MFA_ALREADY_ENROLLED": {
            "vi": "Xác thực hai lớp đã được thiết lập",
            "en": "Two-factor authentication is already set up",
        },
    }
)


class InvalidCredentialsError(AppError):
    code = "INVALID_CREDENTIALS"
    status = 401


class AccountLockedError(AppError):
    code = "ACCOUNT_LOCKED"
    status = 403


class AccountDisabledError(AppError):
    code = "ACCOUNT_DISABLED"
    status = 403


class EmailNotVerifiedError(AppError):
    code = "EMAIL_NOT_VERIFIED"
    status = 403


class SessionExpiredError(AppError):
    code = "SESSION_EXPIRED"
    status = 401


class PasswordPolicyError(AppError):
    code = "PASSWORD_POLICY"
    status = 422

    def __init__(self, violations: list[tuple[str, str]], field: str = "password") -> None:
        super().__init__(errors=[FieldError(field, code, message) for code, message in violations])


class PasswordIncorrectError(AppError):
    code = "PASSWORD_INCORRECT"
    status = 422


class VerificationCodeInvalidError(AppError):
    code = "VERIFICATION_CODE_INVALID"
    status = 422


class TokenInvalidError(AppError):
    code = "TOKEN_INVALID"
    status = 422


class PinNotSetError(AppError):
    code = "PIN_NOT_SET"
    status = 422


class PinAlreadySetError(AppError):
    code = "PIN_ALREADY_SET"
    status = 409


class PinWeakError(AppError):
    code = "PIN_WEAK"
    status = 422


class PinInvalidError(AppError):
    code = "PIN_INVALID"
    status = 422

    def __init__(self, remaining_attempts: int) -> None:
        super().__init__(f"remaining_attempts={remaining_attempts}")
        self.remaining_attempts = remaining_attempts


class PinLockedError(AppError):
    code = "PIN_LOCKED"
    status = 422

    def __init__(self, *, requires_reset: bool, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(
            "reset_required" if requires_reset else "temporarily_locked", headers=headers
        )
        self.requires_reset = requires_reset


class MfaRequiredError(AppError):
    code = "MFA_REQUIRED"
    status = 403


class MfaEnrollmentRequiredError(AppError):
    code = "MFA_ENROLLMENT_REQUIRED"
    status = 403


class MfaInvalidError(AppError):
    code = "MFA_INVALID"
    status = 422


class MfaStepUpRequiredError(AppError):
    code = "MFA_STEP_UP_REQUIRED"
    status = 403


class MfaAlreadyEnrolledError(AppError):
    code = "MFA_ALREADY_ENROLLED"
    status = 409


register_titles(
    {
        "PHONE_TAKEN": {
            "vi": "Số điện thoại đã được tài khoản khác dùng",
            "en": "The phone number is used by another account",
        },
        "PROFILE_NAME_LOCKED": {
            "vi": "Họ tên đã được xác minh (KYC), không đổi được",
            "en": "The name is verified (KYC) and cannot be changed",
        },
    }
)


class PhoneTakenError(ConflictError):
    code = "PHONE_TAKEN"


class ProfileNameLockedError(ConflictError):
    code = "PROFILE_NAME_LOCKED"

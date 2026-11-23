"""Request/response models for identity endpoints (explicit per audience; no ORM dumping)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Locale = Literal["vi", "en"]
Password = Field(min_length=1, max_length=128)
Pin = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
Code = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class RegisterRequest(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Password
    full_name: str = Field(min_length=2, max_length=120)
    locale: Locale = "vi"


class AcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"


class VerifyEmailRequest(StrictModel):
    email: EmailStr = Field(max_length=254)
    code: str = Code


class EmailOnlyRequest(StrictModel):
    email: EmailStr = Field(max_length=254)


class LoginRequest(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Password


class ResetPasswordRequest(StrictModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Password


class PinStatusResponse(BaseModel):
    is_set: bool
    locked_until: datetime | None
    requires_reset: bool


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    display_name: str | None
    locale: Locale
    user_type: Literal["CUSTOMER", "STAFF"]
    email_verified: bool
    created_at: datetime
    permissions: list[str]
    pin: PinStatusResponse
    kyc_tier: int = Field(description="0 = unverified, 1 = verified (simulated KYC)")


class UpdateProfileRequest(StrictModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    display_name: str | None = Field(default=None, max_length=60)
    locale: Locale | None = None


class ChangePasswordRequest(StrictModel):
    current_password: str = Password
    new_password: str = Password


class ChangeEmailRequest(StrictModel):
    password: str = Password
    new_email: EmailStr = Field(max_length=254)


class CodeRequest(StrictModel):
    code: str = Code


class SessionResponse(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


class LoginHistoryItem(BaseModel):
    at: datetime
    result: str
    ip: str | None
    user_agent: str | None


class SetPinRequest(StrictModel):
    password: str = Password
    pin: str = Pin


class ChangePinRequest(StrictModel):
    current_pin: str = Pin
    new_pin: str = Pin


class PasswordOnlyRequest(StrictModel):
    password: str = Password


class ConfirmPinResetRequest(StrictModel):
    code: str = Code
    new_pin: str = Pin


class LogoutAllResponse(BaseModel):
    revoked_sessions: int


# --------------------------------------------------------------------------- staff


class StaffLoginResponse(BaseModel):
    next_step: Literal["MFA_REQUIRED", "MFA_ENROLLMENT_REQUIRED"]


class MfaEnrollmentResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmResponse(BaseModel):
    recovery_codes: list[str]


class MfaVerifyRequest(StrictModel):
    code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    recovery_code: str | None = Field(default=None, min_length=10, max_length=20)


class StaffMeResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    locale: Locale
    roles: list[str]
    permissions: list[str]
    mfa_verified_at: datetime | None
    session_expires_at: datetime

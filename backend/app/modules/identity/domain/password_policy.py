"""Password policy (security.md §2.1, NIST SP 800-63B style).

Length 10-128, no composition rules, reject very common passwords and passwords built from
the user's own e-mail or name. The common-password list below is a small built-in list of
widely known weak passwords; it can be replaced by a larger offline list without code change.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

MIN_LENGTH = 10
MAX_LENGTH = 128
MIN_DISTINCT_CHARACTERS = 3
MIN_PERSONAL_TOKEN_LENGTH = 4

COMMON_PASSWORDS = frozenset(
    {
        "1234567890",
        "12345678910",
        "0123456789",
        "1111111111",
        "0000000000",
        "1234512345",
        "123456789a",
        "a123456789",
        "qwertyuiop",
        "asdfghjkl1",
        "zxcvbnm123",
        "1q2w3e4r5t",
        "q1w2e3r4t5",
        "1qaz2wsx3edc",
        "qazwsxedc1",
        "password12",
        "password123",
        "password1234",
        "passw0rd123",
        "p@ssw0rd123",
        "iloveyou12",
        "iloveyou123",
        "abc1234567",
        "abcd123456",
        "abcdefghij",
        "letmein123",
        "welcome123",
        "welcome1234",
        "admin12345",
        "administrator",
        "changeme123",
        "football12",
        "baseball12",
        "superman12",
        "princess12",
        "sunshine12",
        "dragon1234",
        "monkey1234",
        "master1234",
        "trustno1234",
        "starwars12",
        "qwerty1234",
        "qwerty12345",
        "qwerty123456",
        "1234qwerty",
        "11223344556",
        "aaaaaaaaaa",
        "matkhau123",
        "matkhau1234",
        "anhyeuem123",
        "anhyeuem12",
        "emyeuanh123",
        "123456aA@",
        "Aa123456789",
        "vietnam123",
        "vietnam1234",
        "hanoi12345",
        "saigon1234",
        "viewallet1",
        "viewallet123",
    }
)


@dataclass(frozen=True, slots=True)
class PasswordViolation:
    code: str
    message: str


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def check_password(password: str, *, email: str, full_name: str = "") -> list[PasswordViolation]:
    """Return all violations (empty list = acceptable)."""
    violations: list[PasswordViolation] = []
    if len(password) < MIN_LENGTH:
        violations.append(PasswordViolation("TOO_SHORT", f"at least {MIN_LENGTH} characters"))
    if len(password) > MAX_LENGTH:
        violations.append(PasswordViolation("TOO_LONG", f"at most {MAX_LENGTH} characters"))
    normalized = _normalize(password)
    if normalized in COMMON_PASSWORDS or len(set(normalized)) < MIN_DISTINCT_CHARACTERS:
        violations.append(PasswordViolation("TOO_COMMON", "this password is too easy to guess"))
    local_part = _normalize(email.split("@", 1)[0])
    personal = [local_part, *(_normalize(p) for p in full_name.split())]
    if any(len(token) >= MIN_PERSONAL_TOKEN_LENGTH and token in normalized for token in personal):
        violations.append(
            PasswordViolation("CONTAINS_PERSONAL_INFO", "must not contain your e-mail or name")
        )
    return violations

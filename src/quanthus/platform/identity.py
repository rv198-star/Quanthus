"""Local password, session, MFA, RBAC, and security-audit services."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, cast
from urllib.parse import quote

from quanthus.platform.models import PLATFORM_ROLES, SecurityAuditRecord, SessionRecord, UserRecord
from quanthus.platform.persistence import PlatformStore

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
SESSION_TTL = timedelta(hours=12)
FAILED_LOGIN_WINDOW = timedelta(minutes=15)
FAILED_LOGIN_LIMIT = 5
ROLE_PERMISSIONS = {
    "admin": frozenset(
        {
            "users:read",
            "users:write",
            "gates:read",
            "jobs:read",
            "jobs:run",
            "product:read",
            "product:review",
            "product:govern",
        }
    ),
    "operator": frozenset(
        {"gates:read", "jobs:read", "jobs:run", "product:read", "product:review"}
    ),
    "viewer": frozenset({"gates:read", "jobs:read", "product:read"}),
}


class AuthenticationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


class MfaRequiredError(AuthenticationError):
    pass


class SecretCipher(Protocol):
    def encrypt(self, value: str) -> str: ...

    def decrypt(self, value: str) -> str: ...


class FernetSecretCipher:
    def __init__(self, key: str) -> None:
        try:
            from cryptography.fernet import Fernet  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install Quanthus with the platform dependency group") from exc
        self._fernet: Any = Fernet(key.encode("ascii"))

    def encrypt(self, value: str) -> str:
        return cast(str, self._fernet.encrypt(value.encode("ascii")).decode("ascii"))

    def decrypt(self, value: str) -> str:
        return cast(str, self._fernet.decrypt(value.encode("ascii")).decode("ascii"))


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: str
    username: str
    role: str
    session_id: str

    def permits(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


@dataclass(frozen=True)
class LoginResult:
    token: str
    expires_at: datetime
    principal: AuthenticatedPrincipal


@dataclass(frozen=True)
class MfaEnrollment:
    secret: str
    provisioning_uri: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_username(value: str) -> tuple[str, str]:
    username = value.strip()
    if not 3 <= len(username) <= 64 or any(character.isspace() for character in username):
        raise ValueError("username must be 3-64 non-whitespace characters")
    return username, username.casefold()


def _validate_password(value: str) -> None:
    if not 7 <= len(value) <= 256:
        raise ValueError("password must be 7-256 characters")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    _validate_password(password)
    observed_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        observed_salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        (
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(observed_salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, expected_raw = encoded.split("$", 3)
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_raw.encode("ascii"))
    except (ValueError, UnicodeError):
        return False
    if algorithm != PASSWORD_ALGORITHM or iterations != PASSWORD_ITERATIONS:
        return False
    observed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(observed, expected)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, *, at: int | None = None, step_seconds: int = 30) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret.upper() + padding).encode("ascii"), casefold=True)
    counter = int(at if at is not None else time.time()) // step_seconds
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def verify_totp(secret: str, code: str, *, at: int | None = None) -> bool:
    if len(code) != 6 or not code.isdigit():
        return False
    observed_at = int(at if at is not None else time.time())
    return any(
        hmac.compare_digest(totp_code(secret, at=observed_at + offset), code)
        for offset in (-30, 0, 30)
    )


def public_user(user: UserRecord) -> dict[str, object]:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
        "active": user.active,
        "mfa_enabled": user.mfa_enabled,
        "created_at": user.created_at.isoformat(),
    }


class IdentityService:
    def __init__(
        self,
        store: PlatformStore,
        cipher: SecretCipher,
        *,
        now: Callable[[], datetime] = _utc_now,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._now = now
        self._wall_clock = wall_clock
        self._dummy_hash = hash_password("Quanthus-dummy-password-123")

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str,
        actor_user_id: str | None = None,
        request_id: str | None = None,
    ) -> UserRecord:
        normalized, _ = _normalize_username(username)
        if role not in PLATFORM_ROLES:
            raise ValueError(f"role must be one of {PLATFORM_ROLES}")
        user = UserRecord(
            user_id=f"usr_{uuid.uuid4().hex}",
            username=normalized,
            password_hash=hash_password(password),
            role=role,
            active=True,
            mfa_secret_ciphertext=None,
            mfa_enabled=False,
            created_at=self._now(),
        )
        self._store.create_user(user)
        self._audit(
            "user.created",
            "allowed",
            actor_user_id=actor_user_id,
            subject_user_id=user.user_id,
            request_id=request_id,
            details={"role": role},
        )
        return user

    def login(
        self,
        *,
        username: str,
        password: str,
        mfa_code: str | None,
        request_id: str | None,
    ) -> LoginResult:
        try:
            _, username_key = _normalize_username(username)
        except ValueError:
            username_key = username.strip().casefold()
        now = self._now()
        if self._store.failed_login_count(username_key, now - FAILED_LOGIN_WINDOW) >= (
            FAILED_LOGIN_LIMIT
        ):
            self._audit_login(None, username_key, "denied", "login_rate_limited", request_id)
            raise AuthenticationError("login_rate_limited")
        user = self._store.get_user_by_username(username_key)
        password_hash = user.password_hash if user is not None else self._dummy_hash
        password_ok = verify_password(password, password_hash)
        if user is None or not user.active or not password_ok:
            self._audit_login(user, username_key, "denied", "invalid_credentials", request_id)
            raise AuthenticationError("invalid_credentials")
        if user.mfa_enabled:
            if mfa_code is None:
                self._audit_login(user, username_key, "denied", "mfa_required", request_id)
                raise MfaRequiredError("mfa_required")
            ciphertext = user.mfa_secret_ciphertext
            if ciphertext is None or not verify_totp(
                self._cipher.decrypt(ciphertext),
                mfa_code,
                at=int(self._wall_clock()),
            ):
                self._audit_login(user, username_key, "denied", "invalid_mfa", request_id)
                raise AuthenticationError("invalid_mfa")
        token = secrets.token_urlsafe(32)
        session = SessionRecord(
            session_id=f"ses_{uuid.uuid4().hex}",
            user_id=user.user_id,
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            expires_at=now + SESSION_TTL,
            created_at=now,
        )
        self._store.create_session(session)
        self._audit_login(user, username_key, "allowed", "authenticated", request_id)
        return LoginResult(
            token=token,
            expires_at=session.expires_at,
            principal=AuthenticatedPrincipal(
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                session_id=session.session_id,
            ),
        )

    def authenticate(self, token: str) -> AuthenticatedPrincipal:
        if not token:
            raise AuthenticationError("authentication_required")
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        session = self._store.get_session(token_hash)
        if session is None or session.revoked_at is not None or session.expires_at <= self._now():
            raise AuthenticationError("invalid_or_expired_session")
        user = self._store.get_user(session.user_id)
        if user is None or not user.active:
            raise AuthenticationError("invalid_or_expired_session")
        return AuthenticatedPrincipal(
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            session_id=session.session_id,
        )

    def require(self, principal: AuthenticatedPrincipal, permission: str) -> None:
        if not principal.permits(permission):
            raise AuthorizationError("permission_denied")

    def logout(self, token: str, *, request_id: str | None = None) -> bool:
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        session = self._store.get_session(token_hash)
        revoked = self._store.revoke_session(token_hash, self._now())
        self._audit(
            "logout",
            "allowed" if revoked else "ignored",
            actor_user_id=session.user_id if session else None,
            subject_user_id=session.user_id if session else None,
            request_id=request_id,
            details={},
        )
        return revoked

    def begin_mfa(self, principal: AuthenticatedPrincipal) -> MfaEnrollment:
        user = self._store.get_user(principal.user_id)
        if user is None:
            raise AuthenticationError("invalid_or_expired_session")
        if user.mfa_enabled:
            raise AuthenticationError("mfa_already_enabled")
        secret = generate_totp_secret()
        self._store.set_user_mfa(principal.user_id, self._cipher.encrypt(secret), False)
        label = quote(f"Quanthus:{principal.username}")
        uri = f"otpauth://totp/{label}?secret={secret}&issuer=Quanthus&digits=6&period=30"
        return MfaEnrollment(secret=secret, provisioning_uri=uri)

    def confirm_mfa(
        self,
        principal: AuthenticatedPrincipal,
        code: str,
        *,
        request_id: str | None = None,
    ) -> None:
        user = self._store.get_user(principal.user_id)
        if user is None or user.mfa_secret_ciphertext is None:
            raise AuthenticationError("mfa_enrollment_missing")
        secret = self._cipher.decrypt(user.mfa_secret_ciphertext)
        if not verify_totp(secret, code, at=int(self._wall_clock())):
            raise AuthenticationError("invalid_mfa")
        self._store.set_user_mfa(user.user_id, user.mfa_secret_ciphertext, True)
        self._audit(
            "mfa.enabled",
            "allowed",
            actor_user_id=user.user_id,
            subject_user_id=user.user_id,
            request_id=request_id,
            details={},
        )

    def disable_mfa(
        self,
        principal: AuthenticatedPrincipal,
        code: str,
        *,
        request_id: str | None = None,
    ) -> None:
        user = self._store.get_user(principal.user_id)
        if (
            user is None
            or not user.mfa_enabled
            or user.mfa_secret_ciphertext is None
            or not verify_totp(
                self._cipher.decrypt(user.mfa_secret_ciphertext),
                code,
                at=int(self._wall_clock()),
            )
        ):
            raise AuthenticationError("invalid_mfa")
        self._store.set_user_mfa(user.user_id, None, False)
        self._audit(
            "mfa.disabled",
            "allowed",
            actor_user_id=user.user_id,
            subject_user_id=user.user_id,
            request_id=request_id,
            details={},
        )

    def _audit_login(
        self,
        user: UserRecord | None,
        username_key: str,
        outcome: str,
        reason: str,
        request_id: str | None,
    ) -> None:
        self._audit(
            "login",
            outcome,
            actor_user_id=user.user_id if user else None,
            subject_user_id=user.user_id if user else None,
            request_id=request_id,
            details={"username_key": username_key, "reason": reason},
        )

    def _audit(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: str | None,
        subject_user_id: str | None,
        request_id: str | None,
        details: dict[str, object],
    ) -> None:
        self._store.append_security_audit(
            SecurityAuditRecord(
                audit_id=f"aud_{uuid.uuid4().hex}",
                event_type=event_type,
                outcome=outcome,
                actor_user_id=actor_user_id,
                subject_user_id=subject_user_id,
                request_id=request_id,
                details=details,
                created_at=self._now(),
            )
        )

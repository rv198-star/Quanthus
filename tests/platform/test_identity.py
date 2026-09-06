from __future__ import annotations

from quanthus.platform.identity import AuthenticationError, AuthorizationError, totp_code
from quanthus.platform.persistence import InMemoryPlatformStore

from .conftest import TestCipher


def test_seven_character_numeric_operator_password_is_supported(kernel) -> None:
    user = kernel.identity.create_user(
        username="simple-admin",
        password="2017655",
        role="admin",
    )
    result = kernel.identity.login(
        username="simple-admin",
        password="2017655",
        mfa_code=None,
        request_id="simple-password-test",
    )

    assert result.principal.user_id == user.user_id


def test_password_session_rbac_and_security_audit(kernel) -> None:
    store = kernel.store
    assert isinstance(store, InMemoryPlatformStore)
    viewer = kernel.identity.create_user(
        username="viewer",
        password="Viewer-password-123",
        role="viewer",
    )
    result = kernel.identity.login(
        username="viewer",
        password="Viewer-password-123",
        mfa_code=None,
        request_id="identity-test",
    )

    assert result.principal.user_id == viewer.user_id
    kernel.identity.require(result.principal, "gates:read")
    kernel.identity.require(result.principal, "catalog:read")
    try:
        kernel.identity.require(result.principal, "jobs:run")
    except AuthorizationError as exc:
        assert str(exc) == "permission_denied"
    else:
        raise AssertionError("viewer unexpectedly received jobs:run")
    assert kernel.identity.logout(result.token, request_id="identity-test") is True
    try:
        kernel.identity.authenticate(result.token)
    except AuthenticationError:
        pass
    else:
        raise AssertionError("revoked token remained valid")
    assert any(audit.event_type == "login" for audit in store.security_audits)


def test_mfa_enrollment_changes_subsequent_login(kernel, monkeypatch) -> None:
    observed_time = 1_800_000_000
    monkeypatch.setattr(kernel.identity, "_wall_clock", lambda: observed_time)
    first = kernel.identity.login(
        username="admin",
        password="Admin-password-123",
        mfa_code=None,
        request_id="mfa-test",
    )
    enrollment = kernel.identity.begin_mfa(first.principal)
    kernel.identity.confirm_mfa(
        first.principal,
        totp_code(enrollment.secret, at=observed_time),
        request_id="mfa-test",
    )

    try:
        kernel.identity.login(
            username="admin",
            password="Admin-password-123",
            mfa_code=None,
            request_id="mfa-test-2",
        )
    except AuthenticationError as exc:
        assert str(exc) == "mfa_required"
    else:
        raise AssertionError("MFA-enabled login did not require a code")
    result = kernel.identity.login(
        username="admin",
        password="Admin-password-123",
        mfa_code=totp_code(enrollment.secret, at=observed_time),
        request_id="mfa-test-3",
    )
    assert result.principal.username == "admin"
    try:
        kernel.identity.begin_mfa(result.principal)
    except AuthenticationError as exc:
        assert str(exc) == "mfa_already_enabled"
    else:
        raise AssertionError("enabled MFA secret was replaceable without disable verification")


def test_cipher_fixture_does_not_leak_plain_secret() -> None:
    cipher = TestCipher()
    ciphertext = cipher.encrypt("ABCDEF")
    assert ciphertext != "ABCDEF"
    assert cipher.decrypt(ciphertext) == "ABCDEF"

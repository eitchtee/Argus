import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet() -> Fernet:
    secret_key = str(settings.SECRET_KEY).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key).digest())
    return Fernet(key)


class EncryptedTextField(models.TextField):
    """A text field whose non-empty database value is encrypted with Fernet."""

    description = "Encrypted text"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        try:
            return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise ImproperlyConfigured(
                "Unable to decrypt a Trakt token. Reconnect the Trakt account "
                "after restoring the SECRET_KEY used to encrypt it."
            ) from exc

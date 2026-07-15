from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.users.managers import UserManager


class User(AbstractUser):
    username = None
    email = models.EmailField(_("E-mail"), unique=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class UserSettings(models.Model):
    user = models.OneToOneField(
        get_user_model(), on_delete=models.CASCADE, related_name="settings"
    )
    language = models.CharField(
        max_length=10,
        default="auto",
        verbose_name=_("Language"),
    )
    tvdb_metadata_language = models.CharField(
        max_length=16,
        default="eng",
        verbose_name=_("TV metadata language"),
    )
    tmdb_metadata_language = models.CharField(
        max_length=16,
        default="en-US",
        verbose_name=_("Movie metadata language"),
    )
    timezone = models.CharField(
        max_length=50,
        default="auto",
        verbose_name=_("Time Zone"),
    )
    date_format = models.CharField(
        max_length=100,
        default="SHORT_DATE_FORMAT",
        verbose_name=_("Date Format"),
    )

    def __str__(self):
        return f"{self.user.email}'s settings"

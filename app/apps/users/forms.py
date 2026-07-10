import pytz
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.utils.translation import gettext_lazy as _

from apps.users.models import UserSettings

TIMEZONE_CHOICES = (("auto", _("Auto")),) + tuple((tz, tz) for tz in pytz.all_timezones)


class LoginForm(AuthenticationForm):
    username = UsernameField(
        label=_("E-mail"),
        widget=forms.EmailInput(
            attrs={
                "class": "input",
                "placeholder": _("E-mail"),
                "name": "email",
                "autocomplete": "email",
            }
        ),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "input",
                "placeholder": _("Password"),
                "autocomplete": "current-password",
            }
        ),
    )

    error_messages = {
        "invalid_login": _("Invalid e-mail or password"),
        "inactive": _("This account is deactivated"),
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)

        self.helper = FormHelper()
        self.helper.layout = Layout(
            "username",
            "password",
            Submit("Submit", _("Login"), css_class="w-full mt-3"),
        )


class UserSettingsForm(forms.ModelForm):
    DATE_FORMAT_CHOICES = [
        ("SHORT_DATE_FORMAT", _("Default")),
        ("d-m-Y", "20-01-2025"),
        ("m-d-Y", "01-20-2025"),
        ("Y-m-d", "2025-01-20"),
        ("d/m/Y", "20/01/2025"),
        ("m/d/Y", "01/20/2025"),
        ("Y/m/d", "2025/01/20"),
        ("d.m.Y", "20.01.2025"),
        ("m.d.Y", "01.20.2025"),
        ("Y.m.d", "2025.01.20"),
    ]

    LANGUAGE_CHOICES = (("auto", _("Auto")),) + settings.LANGUAGES

    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial="auto", label=_("Language"))
    timezone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES, initial="auto", label=_("Time Zone")
    )
    date_format = forms.ChoiceField(
        choices=DATE_FORMAT_CHOICES, initial="SHORT_DATE_FORMAT", label=_("Date Format")
    )

    class Meta:
        model = UserSettings
        fields = ["language", "timezone", "date_format"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "language",
            "timezone",
            "date_format",
            Submit("submit", _("Save"), css_class="btn btn-primary"),
        )
import pytz
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Fieldset, HTML, Layout, Submit
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.utils.translation import gettext_lazy as _

from apps.users.models import UserSettings
from apps.catalog.languages import get_language_choices

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
    tvdb_metadata_language = forms.ChoiceField(label=_("TV metadata language"))
    tmdb_metadata_language = forms.ChoiceField(label=_("Movie metadata language"))
    timezone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES, initial="auto", label=_("Time Zone")
    )
    date_format = forms.ChoiceField(
        choices=DATE_FORMAT_CHOICES, initial="SHORT_DATE_FORMAT", label=_("Date Format")
    )

    class Meta:
        model = UserSettings
        fields = [
            "language",
            "tvdb_metadata_language",
            "tmdb_metadata_language",
            "timezone",
            "date_format",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._set_metadata_language_choices("tvdb")
        self._set_metadata_language_choices("tmdb")

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "language",
            Fieldset(
                _("Metadata"),
                HTML(
                    '<p class="text-sm text-base-content/70 mb-3">'
                    + str(
                        _(
                            "Controls titles and descriptions from metadata providers. "
                            "This does not change the interface language."
                        )
                    )
                    + "</p>"
                ),
                "tvdb_metadata_language",
                "tmdb_metadata_language",
            ),
            "timezone",
            "date_format",
            Submit("submit", _("Save"), css_class="btn btn-primary"),
        )

    def _set_metadata_language_choices(self, provider):
        field_name = f"{provider}_metadata_language"
        choices = list(get_language_choices(provider))
        current = getattr(self.instance, field_name, "")
        if current and current not in {value for value, _label in choices}:
            choices.append((current, current))
        self.fields[field_name].choices = choices

from django import forms
from django.utils.translation import gettext_lazy as _


class SearchForm(forms.Form):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": _("Search movies or TV shows..."),
                "autocomplete": "off",
            }
        ),
    )
    type = forms.ChoiceField(
        label=_("Type"),
        choices=[("movie", _("Movies")), ("tv", _("TV Shows"))],
        initial="movie",
        widget=forms.Select(attrs={"class": "select select-bordered"}),
    )
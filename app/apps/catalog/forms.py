from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.artwork import media_language_for_user
from apps.catalog.languages import (
    get_language_choices,
    language_codes_match,
    language_base_code,
    language_display_name,
)
from apps.catalog.models import MediaArtwork


class SearchForm(forms.Form):
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered h-10 min-h-10 w-full",
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
    provider = forms.ChoiceField(
        label=_("Provider"),
        choices=[("tmdb", "TMDB"), ("tvdb", "TVDB")],
        initial="tmdb",
        widget=forms.Select(
            attrs={
                "class": "select select-bordered",
                "aria-label": _("Search provider"),
            }
        ),
    )


class MediaArtworkPreferenceForm(forms.Form):
    language = forms.ChoiceField(
        label=_("Metadata language"),
        required=False,
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )
    poster_artwork_id = forms.ChoiceField(
        label=_("Poster"),
        required=False,
        choices=(),
        widget=forms.HiddenInput,
    )
    background_artwork_id = forms.ChoiceField(
        label=_("Background"),
        required=False,
        choices=(),
        widget=forms.HiddenInput,
    )

    def __init__(self, *, media, user, artworks, preference=None, data=None):
        super().__init__(data=data)
        self.media_item = media
        self.user = user
        self.preference = preference
        identity = {
            "provider": media.provider,
            "media_type": (
                MediaArtwork.MediaType.MOVIE
                if media.__class__.__name__ == "Movie"
                else MediaArtwork.MediaType.TV
            ),
            "external_id": str(media.external_id),
        }
        self.identity = identity
        language = (
            preference.language
            if preference and preference.language
            else media_language_for_user(user, media)
        )
        language_choices = _language_choices_for_media(media, artworks)
        language_choice_codes = {code for code, _label in language_choices}
        if language and language not in language_choice_codes:
            available_codes = set((getattr(media, "translations", {}) or {}).keys())
            available_codes.update(
                artwork.language for artwork in artworks if artwork.language
            )
            language_choices.insert(
                0,
                (
                    language,
                    language_display_name(language)
                    if any(
                        language_codes_match(language, available_code)
                        for available_code in available_codes
                    )
                    else _("Unavailable (fallback active)"),
                ),
            )
        self.fields["language"].choices = [
            ("", _("Use profile language")),
            *language_choices,
        ]
        self.fields["language"].initial = preference.language if preference else ""

        poster_choices = [
            ("", _("Automatic")),
            *[(str(item.id), str(item.id)) for item in artworks if item.kind == MediaArtwork.Kind.POSTER],
        ]
        background_choices = [
            ("", _("Automatic")),
            *[(str(item.id), str(item.id)) for item in artworks if item.kind == MediaArtwork.Kind.BACKGROUND],
        ]
        self.fields["poster_artwork_id"].choices = poster_choices
        self.fields["background_artwork_id"].choices = background_choices
        self.fields["poster_artwork_id"].initial = (
            str(preference.poster_artwork_id)
            if preference and preference.poster_artwork_id
            else ""
        )
        self.fields["background_artwork_id"].initial = (
            str(preference.background_artwork_id)
            if preference and preference.background_artwork_id
            else ""
        )

    def clean_poster_artwork_id(self):
        return self._clean_artwork_id(
            self.cleaned_data.get("poster_artwork_id"),
            MediaArtwork.Kind.POSTER,
        )

    def clean_background_artwork_id(self):
        return self._clean_artwork_id(
            self.cleaned_data.get("background_artwork_id"),
            MediaArtwork.Kind.BACKGROUND,
        )

    def _clean_artwork_id(self, value, kind):
        if not value:
            return None
        try:
            artwork_id = int(value)
        except (TypeError, ValueError) as exc:
            raise forms.ValidationError(_("Invalid artwork selection.")) from exc
        artwork = MediaArtwork.objects.filter(
            id=artwork_id,
            kind=kind,
            **self.identity,
        ).first()
        if artwork is None:
            raise forms.ValidationError(_("That artwork is no longer available."))
        return artwork


def _language_choices_for_media(media, artworks):
    choices = []
    known_bases = set()
    for code, label in get_language_choices(media.provider):
        base = language_base_code(code)
        if base in known_bases:
            continue
        choices.append((code, language_display_name(code, label)))
        known_bases.add(base)

    available_codes = set((getattr(media, "translations", {}) or {}).keys())
    available_codes.update(
        artwork.language for artwork in artworks if artwork.language
    )
    choice_codes = {code for code, _label in choices}
    for code in sorted(available_codes - choice_codes):
        base = language_base_code(code)
        if base not in known_bases and not any(
            language_codes_match(code, choice_code) for choice_code in choice_codes
        ):
            choices.append((code, language_display_name(code)))
            known_bases.add(base)
            choice_codes.add(code)
    return choices

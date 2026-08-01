from django import forms
from django.core.exceptions import ValidationError

from apps.imports.services import (
    MAX_ARCHIVE_SIZE,
    TraktExportError,
    validate_trakt_export,
)


class TraktImportForm(forms.Form):
    archive = forms.FileField(
        label="Trakt export ZIP",
        help_text="Upload the ZIP downloaded from Trakt's data export page.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".zip,application/zip",
                "class": "file-input file-input-bordered w-full",
            }
        ),
    )

    def clean_archive(self):
        archive = self.cleaned_data["archive"]
        if archive.size > MAX_ARCHIVE_SIZE:
            raise ValidationError("The archive exceeds the maximum upload size.")
        if not archive.name.lower().endswith(".zip"):
            raise ValidationError("Choose a ZIP file exported by Trakt.")
        try:
            validate_trakt_export(archive)
        except TraktExportError as exc:
            raise ValidationError(str(exc)) from exc
        finally:
            archive.seek(0)
        return archive

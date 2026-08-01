import json
from io import BytesIO
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.imports.forms import TraktImportForm
from apps.imports.models import ImportJob
from apps.imports.services import TraktExportError
from apps.trakt.sync import SyncReport


def zip_bytes(**members):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, json.dumps(payload))
    return stream.getvalue()


class TraktImportFormTests(SimpleTestCase):
    def test_validates_a_trakt_zip_upload(self):
        form = TraktImportForm(
            files={
                "archive": SimpleUploadedFile(
                    "trakt-export.zip",
                    zip_bytes(**{"watched-shows.json": []}),
                    content_type="application/zip",
                )
            }
        )

        self.assertTrue(form.is_valid())

    def test_rejects_non_zip_uploads(self):
        form = TraktImportForm(
            files={
                "archive": SimpleUploadedFile(
                    "trakt-export.json",
                    b"{}",
                    content_type="application/json",
                )
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("archive", form.errors)

    def test_rejects_an_archive_that_exceeds_the_upload_limit(self):
        with patch("apps.imports.forms.MAX_ARCHIVE_SIZE", 1):
            form = TraktImportForm(
                files={
                    "archive": SimpleUploadedFile(
                        "trakt-export.zip",
                        zip_bytes(**{"watched-shows.json": []}),
                        content_type="application/zip",
                    )
                }
            )
            self.assertFalse(form.is_valid())
            self.assertIn("maximum upload size", form.errors["archive"][0])


class TraktImportTaskTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_directory = TemporaryDirectory()
        cls.media_settings = override_settings(
            MEDIA_ROOT=cls.media_directory.name,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            },
        )
        cls.media_settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_settings.disable()
        cls.media_directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user("import@example.com")

    def create_job(self):
        return ImportJob.objects.create(
            user=self.user,
            service=ImportJob.Service.TRAKT,
            source_file=SimpleUploadedFile(
                "trakt-export.zip",
                zip_bytes(**{"watched-shows.json": []}),
                content_type="application/zip",
            ),
        )

    @patch("apps.imports.tasks.import_trakt_export")
    def test_task_persists_success_counts_and_deletes_archive(self, import_export):
        job = self.create_job()
        source_name = job.source_file.name
        import_export.return_value = SyncReport(
            movies_imported=2,
            shows_imported=3,
            episodes_marked=4,
            warnings=["Skipped one item"],
        )

        from apps.imports.tasks import import_trakt_job

        import_trakt_job.func(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.SUCCEEDED)
        self.assertEqual(job.movies_imported, 2)
        self.assertEqual(job.shows_imported, 3)
        self.assertEqual(job.episodes_marked, 4)
        self.assertEqual(job.warning_messages, ["Skipped one item"])
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.source_file.name, "")
        self.assertFalse(default_storage.exists(source_name))

    @patch(
        "apps.imports.tasks.import_trakt_export",
        side_effect=TraktExportError("bad export"),
    )
    def test_task_marks_failure_and_deletes_archive(self, _import_export):
        job = self.create_job()
        source_name = job.source_file.name

        from apps.imports.tasks import import_trakt_job

        with self.assertRaisesMessage(TraktExportError, "bad export"):
            import_trakt_job.func(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertEqual(job.error_message, "bad export")
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.source_file.name, "")
        self.assertFalse(default_storage.exists(source_name))

    def test_recover_stalled_job_marks_failure_and_deletes_archive(self):
        job = self.create_job()
        source_name = job.source_file.name
        started_at = timezone.now() - timedelta(hours=2)
        ImportJob.objects.filter(id=job.id).update(
            status=ImportJob.Status.PROCESSING,
            started_at=started_at,
        )

        from apps.imports.tasks import recover_stalled_imports

        with patch(
            "apps.imports.tasks._STALLED_IMPORT_TIMEOUT",
            timedelta(hours=1),
        ):
            self.assertEqual(recover_stalled_imports(), 1)

        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertIn("worker stopped", job.error_message)
        self.assertEqual(job.source_file.name, "")
        self.assertFalse(default_storage.exists(source_name))

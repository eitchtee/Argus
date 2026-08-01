import json
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.imports.models import ImportJob


def zip_bytes(**members):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, json.dumps(payload))
    return stream.getvalue()


@override_settings(DJANGO_VITE_DEV_MODE=True)
class ImportViewTests(TestCase):
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
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        self.user = get_user_model().objects.create_user(
            "import@example.com",
            password="password",
        )
        self.other_user = get_user_model().objects.create_user(
            "other@example.com",
            password="password",
        )
        self.client.login(username="import@example.com", password="password")

    def tearDown(self):
        from django_vite.core.asset_loader import DjangoViteAssetLoader

        DjangoViteAssetLoader._instance = None
        super().tearDown()

    def upload(self):
        return SimpleUploadedFile(
            "trakt-export.zip",
            zip_bytes(**{"watched-shows.json": []}),
            content_type="application/zip",
        )

    def test_trakt_tab_endpoint_renders_upload_form(self):
        response = self.client.get(reverse("import_trakt"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trakt export ZIP")
        self.assertContains(response, "multipart/form-data")
        self.assertContains(response, f'hx-post="{reverse("import_trakt_upload")}"')

    @patch("apps.imports.views.import_trakt_job")
    def test_upload_queues_job_instead_of_importing_inline(self, import_task):
        import_task.defer.return_value = 42

        response = self.client.post(
            reverse("import_trakt_upload"),
            {"archive": self.upload()},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        job = ImportJob.objects.get(user=self.user)
        self.assertEqual(job.status, ImportJob.Status.QUEUED)
        self.assertEqual(job.task_id, 42)
        import_task.defer.assert_called_once_with(import_job_id=job.id)
        self.assertContains(response, "queued")
        self.assertContains(
            response,
            reverse("import_trakt_status", kwargs={"job_id": job.id}),
        )

    def test_invalid_upload_does_not_create_job(self):
        response = self.client.post(
            reverse("import_trakt_upload"),
            {
                "archive": SimpleUploadedFile(
                    "not-an-export.json",
                    b"{}",
                    content_type="application/json",
                )
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ImportJob.objects.exists())
        self.assertContains(response, "Choose a ZIP file")

    @patch("apps.imports.views.import_trakt_job")
    def test_upload_does_not_queue_when_an_import_is_already_active(self, import_task):
        active_job = ImportJob.objects.create(
            user=self.user,
            service=ImportJob.Service.TRAKT,
            source_file=self.upload(),
            status=ImportJob.Status.PROCESSING,
        )

        response = self.client.post(
            reverse("import_trakt_upload"),
            {"archive": self.upload()},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ImportJob.objects.filter(user=self.user).count(),
            1,
        )
        import_task.defer.assert_not_called()
        self.assertContains(response, "being imported")
        self.assertContains(
            response,
            reverse("import_trakt_status", kwargs={"job_id": active_job.id}),
        )

    def test_status_endpoint_is_scoped_to_logged_in_user(self):
        job = ImportJob.objects.create(
            user=self.other_user,
            service=ImportJob.Service.TRAKT,
            source_file=SimpleUploadedFile("trakt-export.zip", self.upload().read()),
        )

        response = self.client.get(
            reverse("import_trakt_status", kwargs={"job_id": job.id}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)

    def test_settings_page_exposes_import_service_tabs(self):
        response = self.client.get(reverse("user_settings"))

        self.assertContains(response, "Import")
        self.assertContains(response, 'class="tabs tabs-box"')
        self.assertContains(response, 'id="import-trakt-tab"')
        self.assertContains(response, 'hx-trigger="load"')

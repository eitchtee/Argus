from django.template.loader import get_template
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class BaseTemplateTests(SimpleTestCase):
    def test_base_layout_mounts_toast_fragment(self):
        source = get_template("layouts/base.html").template.source

        self.assertIn('{% include "common/fragments/toasts.html" %}', source)

    def test_shared_scripts_include_htmx_error_handler(self):
        scripts_source = get_template("includes/scripts.html").template.source
        handler_source = get_template(
            "includes/scripts/hyperscript/htmx_error_handler.html"
        ).template.source

        self.assertIn(
            "{% include 'includes/scripts/hyperscript/htmx_error_handler.html' %}",
            scripts_source,
        )
        self.assertIn("behavior htmx_error_handler", handler_source)
        self.assertIn("htmx:responseError", handler_source)
        self.assertIn("htmx:afterRequest[detail.failed]", handler_source)
        self.assertIn("htmx:sendError", handler_source)
        self.assertIn("event.detail.xhr.status == 403", handler_source)
        self.assertIn("call location.reload()", handler_source)

    def test_shared_scripts_include_tom_select_initializer(self):
        scripts_source = get_template("includes/scripts.html").template.source
        initializer_source = get_template(
            "includes/scripts/hyperscript/init_tom_select.html"
        ).template.source
        settings_source = get_template(
            "users/fragments/user_settings.html"
        ).template.source

        self.assertIn(
            "{% include 'includes/scripts/hyperscript/init_tom_select.html' %}",
            scripts_source,
        )
        self.assertIn("behavior init_tom_select", initializer_source)
        self.assertIn("TomSelect(it)", initializer_source)
        self.assertIn('_="install init_tom_select"', settings_source)

    def test_settings_modal_uses_top_close_without_closing_backdrop(self):
        settings_source = get_template(
            "users/fragments/user_settings.html"
        ).template.source

        self.assertIn('<dialog open class="modal"', settings_source)
        self.assertIn(
            'class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2"',
            settings_source,
        )
        self.assertIn('aria-label="{% trans \'Close\' %}"', settings_source)
        self.assertNotIn('class="modal-backdrop"', settings_source)
        self.assertNotIn('class="modal-action"', settings_source)

    def test_toasts_listen_for_an_explicit_toast_event(self):
        source = get_template("common/fragments/toasts.html").template.source

        self.assertIn('hx-trigger="toast from:body"', source)
        self.assertNotIn("htmx:afterRequest from:body", source)


class ToastEndpointTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            "user@example.com",
            password="password",
        )
        self.client.login(username="user@example.com", password="password")

    def test_toast_poll_response_does_not_mount_another_poll_trigger(self):
        response = self.client.get(
            reverse("toasts"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="toasts"')
        self.assertNotContains(response, "hx-get=")
        self.assertNotIn("HX-Trigger", response)

    def test_toast_is_coloured_by_level_and_dismissible(self):
        from django.contrib import messages
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        messages.error(request, "Boom", extra_tags="sticky")

        html = render_to_string("common/fragments/toast_messages.html", request=request)

        self.assertIn("toasty alert alert-error", html)
        self.assertIn('data-lucide="circle-alert"', html)
        self.assertIn('_="on click remove closest .toasty"', html)
        self.assertIn('_="init wait 5s then remove me"', html)

from django.template.loader import get_template
from django.test import SimpleTestCase


class BaseTemplateTests(SimpleTestCase):
    def test_base_layout_mounts_toast_fragment(self):
        source = get_template("layouts/base.html").template.source

        self.assertIn('{% include "common/fragments/toasts.html" %}', source)

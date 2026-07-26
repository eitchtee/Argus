from django.test import SimpleTestCase

from apps.movies.services import normalize_movie_status


class MovieStatusTests(SimpleTestCase):
    def test_provider_statuses_normalize_to_movie_statuses(self):
        cases = {
            "Rumored": "Upcoming",
            "Planned": "Upcoming",
            "upcoming": "Upcoming",
            "Announced": "Upcoming",
            "Pre-Production": "Upcoming",
            "pre_production": "Upcoming",
            "In Production": "Upcoming",
            "in_production": "Upcoming",
            "Post Production": "Upcoming",
            "post_production": "Upcoming",
            "Filming / Post-Production": "Upcoming",
            "Completed": "Upcoming",
            "Released": "Released",
            "Canceled": "Canceled",
            "Cancelled": "Canceled",
            "": "Unknown",
            "Something New": "Unknown",
        }

        for raw_status, expected in cases.items():
            with self.subTest(raw_status=raw_status):
                self.assertEqual(normalize_movie_status(raw_status), expected)

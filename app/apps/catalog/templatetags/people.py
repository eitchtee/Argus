from urllib.parse import urlencode

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def person_url(name, tmdb_id=None, tvdb_id=None):
    if tmdb_id:
        return reverse("person-detail", kwargs={"person_id": tmdb_id})
    # No TMDB id (a TVDB credit, or cast stored before ids were kept): resolve on click.
    query = urlencode({key: value for key, value in {"tvdb": tvdb_id, "name": name}.items() if value})
    return f"{reverse('person-lookup')}?{query}"

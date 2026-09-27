from django import template

from apps.mdblist.config import catalog_available_for


register = template.Library()


@register.simple_tag(takes_context=True)
def mdblist_available(context) -> bool:
    """Whether MDBList data can be shown to the viewer: the server has an API
    key, or the viewer connected an account of their own."""
    request = context.get("request")
    return catalog_available_for(getattr(request, "user", None))

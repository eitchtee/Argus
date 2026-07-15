from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.services import search as catalog_search
from apps.catalog.localization import metadata_language_for_user
from apps.catalog.tracking import tracked_keys


class SearchAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        media_type = request.query_params.get("type", "").strip()
        page_value = request.query_params.get("page", "1")
        errors = {}

        if not query:
            errors["q"] = ["This query parameter is required."]
        if media_type not in {"movie", "tv"}:
            errors["type"] = ['Must be "movie" or "tv".']

        try:
            page = int(page_value)
            if page < 1:
                raise ValueError
        except ValueError:
            errors["page"] = ["Must be a positive integer."]

        if errors:
            return Response(errors, status=400)

        provider = "tmdb" if media_type == "movie" else "tvdb"
        language = metadata_language_for_user(request.user, provider)
        results = catalog_search(
            query,
            media_type=media_type,
            language=language,
            page=page,
        )
        tracked = tracked_keys(request.user, media_type, results)

        return Response(
            {
                "results": [
                    {
                        "provider": result.provider,
                        "external_id": result.external_id,
                        "title": result.title,
                        "year": result.year,
                        "poster_url": result.poster_url,
                        "overview": result.overview,
                        "already_tracked": (
                            result.provider,
                            result.external_id,
                        )
                        in tracked,
                    }
                    for result in results
                ]
            }
)


search_view = SearchAPIView.as_view()

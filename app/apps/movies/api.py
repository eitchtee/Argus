from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import NotInDemoMode
from apps.catalog.artwork import (
    media_identity_key,
    original_title_preference_keys,
    use_original_title_for_media,
)
from apps.catalog.localization import metadata_language_for_user, resolve_title
from apps.movies.models import Movie, UserMovie
from apps.movies.services import (
    mark_seen,
    queue_track_movie,
    remove_from_watchlist,
    unmark_seen,
)


class MovieStateSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    provider = serializers.CharField()
    external_id = serializers.CharField()
    title = serializers.CharField()
    poster_path = serializers.CharField(allow_null=True)
    release_date = serializers.DateField(allow_null=True)
    on_watchlist = serializers.BooleanField()
    is_seen = serializers.BooleanField()
    seen_at = serializers.DateTimeField(allow_null=True)


class MovieListResponseSerializer(serializers.Serializer):
    results = MovieStateSerializer(many=True)


class TrackMovieRequestSerializer(serializers.Serializer):
    provider = serializers.CharField()
    external_id = serializers.CharField()


class ErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class MovieListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "watchlist",
                bool,
                OpenApiParameter.QUERY,
                description="When true, return only movies on the current user's watchlist.",
            ),
            OpenApiParameter(
                "seen",
                bool,
                OpenApiParameter.QUERY,
                description="When true, return only movies seen by the current user.",
            ),
        ],
        responses=MovieListResponseSerializer,
    )
    def get(self, request):
        queryset = (
            UserMovie.objects.select_related("movie")
            .filter(user=request.user)
            .order_by("movie__title", "movie_id")
        )

        watchlist = _query_bool(request, "watchlist")
        if watchlist is None and "watchlist" in request.query_params:
            return Response(
                {"watchlist": ["Must be a boolean value."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if watchlist is not None:
            queryset = queryset.filter(on_watchlist=watchlist)

        seen = _query_bool(request, "seen")
        if seen is None and "seen" in request.query_params:
            return Response(
                {"seen": ["Must be a boolean value."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if seen is not None:
            queryset = queryset.filter(is_seen=seen)

        rows = list(queryset)
        original_title_keys = original_title_preference_keys(
            request.user,
            [row.movie for row in rows],
        )
        return Response(
            {
                "results": [
                    _serialize_user_movie(
                        row,
                        use_original_title=media_identity_key(row.movie)
                        in original_title_keys,
                    )
                    for row in rows
                ]
            }
        )


class MovieTrackAPIView(APIView):
    permission_classes = [IsAuthenticated, NotInDemoMode]

    @extend_schema(
        request=TrackMovieRequestSerializer,
        responses={201: MovieStateSerializer, 400: ErrorResponseSerializer},
        description="Track a movie from the selected provider for the current user and add it to their watchlist.",
    )
    def post(self, request):
        serializer = TrackMovieRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user_movie = queue_track_movie(
                request.user,
                serializer.validated_data["provider"],
                serializer.validated_data["external_id"],
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(_serialize_user_movie(user_movie), status=status.HTTP_201_CREATED)


class MovieSeenAPIView(APIView):
    permission_classes = [IsAuthenticated, NotInDemoMode]

    @extend_schema(
        request=None,
        responses={200: MovieStateSerializer, 404: ErrorResponseSerializer},
        description="Mark a movie seen for the current user and remove it from watchlist.",
    )
    def post(self, request, movie_id):
        movie = _get_movie(movie_id)
        if movie is None:
            return _not_found()

        return Response(_serialize_user_movie(mark_seen(request.user, movie)))

    @extend_schema(
        request=None,
        responses={200: MovieStateSerializer, 404: ErrorResponseSerializer},
        description="Mark a movie unseen for the current user.",
    )
    def delete(self, request, movie_id):
        movie = _get_movie(movie_id)
        if movie is None:
            return _not_found()

        return Response(_serialize_user_movie(unmark_seen(request.user, movie)))


class MovieWatchlistAPIView(APIView):
    permission_classes = [IsAuthenticated, NotInDemoMode]

    @extend_schema(
        request=None,
        responses={204: None, 404: ErrorResponseSerializer},
        description="Remove a movie from the current user's watchlist.",
    )
    def delete(self, request, movie_id):
        movie = _get_movie(movie_id)
        if movie is None:
            return _not_found()

        user_movie = remove_from_watchlist(request.user, movie)
        if user_movie is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(_serialize_user_movie(user_movie))


def _query_bool(request, name):
    if name not in request.query_params:
        return None

    value = request.query_params[name].lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _get_movie(movie_id):
    try:
        return Movie.objects.get(id=movie_id)
    except Movie.DoesNotExist:
        return None


def _not_found():
    return Response({"detail": "Movie not found."}, status=status.HTTP_404_NOT_FOUND)


def _serialize_user_movie(user_movie, *, use_original_title=None):
    movie = user_movie.movie
    if use_original_title is None:
        use_original_title = use_original_title_for_media(user_movie.user, movie)
    return {
        "id": movie.id,
        "provider": movie.provider,
        "external_id": movie.external_id,
        "title": resolve_title(
            movie,
            metadata_language_for_user(user_movie.user, movie.provider),
            use_original_title=use_original_title,
        ),
        "poster_path": movie.poster_path,
        "release_date": movie.release_date.isoformat() if movie.release_date else None,
        "on_watchlist": user_movie.on_watchlist,
        "is_seen": user_movie.is_seen,
        "seen_at": user_movie.seen_at.isoformat() if user_movie.seen_at else None,
    }


movie_list_view = MovieListAPIView.as_view()
movie_track_view = MovieTrackAPIView.as_view()
movie_seen_view = MovieSeenAPIView.as_view()
movie_watchlist_view = MovieWatchlistAPIView.as_view()

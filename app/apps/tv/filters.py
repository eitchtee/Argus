import django_filters
from django.utils.translation import gettext_lazy as _

from apps.tv.models import Show
from apps.tv.services import get_watchlist_shows


DEFAULT_CONDITION = "watching"
CONDITION_CHOICES = (
    ("watching", _("Watching")),
    ("completed", _("Completed")),
    ("dropped", _("Dropped")),
    ("paused", _("Paused")),
)
NORMALIZED_STATUS_CHOICES = (
    (Show.NormalizedStatus.CONTINUING, _("Continuing")),
    (Show.NormalizedStatus.ENDED, _("Ended")),
    (Show.NormalizedStatus.UPCOMING, _("Upcoming")),
)


class WatchlistFilter(django_filters.FilterSet):
    condition = django_filters.MultipleChoiceFilter(
        choices=CONDITION_CHOICES,
        method="filter_condition",
        label=_("Condition"),
    )
    normalized_status = django_filters.MultipleChoiceFilter(
        field_name="normalized_status",
        choices=NORMALIZED_STATUS_CHOICES,
        label=_("Show status"),
    )

    class Meta:
        model = Show
        fields = ("condition", "normalized_status")

    def __init__(self, data=None, *args, request=None, **kwargs):
        data = (data or {}).copy()
        condition_values = {
            value for value, _label in CONDITION_CHOICES
        }
        normalized_status_values = {
            value for value, _label in NORMALIZED_STATUS_CHOICES
        }

        conditions = [
            value
            for value in self._get_values(data, "condition")
            if value in condition_values
        ]
        if not conditions:
            conditions = [DEFAULT_CONDITION]
        self._set_values(data, "condition", conditions)

        normalized_statuses = [
            value
            for value in self._get_values(data, "normalized_status")
            if value in normalized_status_values
        ]
        if normalized_statuses:
            self._set_values(data, "normalized_status", normalized_statuses)
        else:
            data.pop("normalized_status", None)
        super().__init__(data, *args, request=request, **kwargs)

    @staticmethod
    def _get_values(data, name):
        if hasattr(data, "getlist"):
            return data.getlist(name)
        value = data.get(name, [])
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value] if value else []

    @staticmethod
    def _set_values(data, name, values):
        if hasattr(data, "setlist"):
            data.setlist(name, values)
        else:
            data[name] = values

    def filter_condition(self, queryset, _name, value):
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            self._condition_shows = []
            return queryset.none()
        condition_shows = {}
        for condition in value or [DEFAULT_CONDITION]:
            for show in get_watchlist_shows(user, condition):
                condition_shows[show.pk] = show
        self._condition_shows = sorted(
            condition_shows.values(),
            key=lambda show: (show.name.casefold(), show.pk),
        )
        return queryset.filter(pk__in=condition_shows)

    @property
    def condition_shows(self):
        if not hasattr(self, "_condition_shows"):
            self.qs
        return self._condition_shows

    @property
    def selected_condition(self):
        return self.selected_conditions[0]

    @property
    def selected_conditions(self):
        self.form.is_valid()
        return self.form.cleaned_data.get("condition") or [DEFAULT_CONDITION]

    @property
    def selected_normalized_status(self):
        statuses = self.selected_normalized_statuses
        return statuses[0] if statuses else None

    @property
    def selected_normalized_statuses(self):
        self.form.is_valid()
        return self.form.cleaned_data.get("normalized_status") or []

from django.conf import settings
from rest_framework.permissions import BasePermission


class NotInDemoMode(BasePermission):
    def has_permission(self, request, view):
        if settings.DEMO and not request.user.is_superuser:
            return False
        return True
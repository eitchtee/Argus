"""
Django System Checks for required environment variables.

This module validates that required environment variables (those without defaults)
are present before the application starts.
"""

import os

from django.core.checks import Error, register


# List of environment variables that are required (no default values)
REQUIRED_ENV_VARS = [
    ("SECRET_KEY", "This is used to provide cryptographic signing."),
]

# List of environment variables that must be valid integers if set
INT_ENV_VARS = [
    ("TASK_WORKERS", "How many workers to have for async tasks."),
    ("SESSION_EXPIRY_TIME", "The age of session cookies, in seconds."),
    ("INTERNAL_PORT", "The port on which the app listens on."),
    ("DJANGO_VITE_DEV_SERVER_PORT", "The port where Vite's dev server is running"),
]


@register()
def check_required_env_vars(app_configs, **kwargs):
    """Check that all required environment variables are set."""
    errors = []

    for var_name, description in REQUIRED_ENV_VARS:
        value = os.getenv(var_name)
        if not value:
            errors.append(
                Error(
                    f"Required environment variable '{var_name}' is not set.",
                    hint=f"{description} Please set this variable in your .env file or environment.",
                    id="argus.E001",
                )
            )

    return errors


@register()
def check_int_env_vars(app_configs, **kwargs):
    """Check that environment variables that should be integers are valid."""
    errors = []

    for var_name, description in INT_ENV_VARS:
        value = os.getenv(var_name)
        if value is not None:
            try:
                int(value)
            except ValueError:
                errors.append(
                    Error(
                        f"Environment variable '{var_name}' must be a valid integer, got '{value}'.",
                        hint=f"{description}",
                        id="argus.E002",
                    )
                )

    return errors

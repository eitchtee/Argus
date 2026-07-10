import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import IntegrityError

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Creates a superuser from environment variables (ADMIN_EMAIL, ADMIN_PASSWORD) "
        "and optionally creates a demo user (demo@demo.com) if settings.DEMO is True."
    )

    def handle(self, *args, **options):
        self.stdout.write("Starting user setup...")

        # --- Create Superuser ---
        admin_email = os.environ.get("ADMIN_EMAIL")
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if admin_email and admin_password:
            self.stdout.write(f"Attempting to create superuser: {admin_email}")
            username_field = User.USERNAME_FIELD

            user_exists_kwargs = {"email": admin_email}
            if username_field != "email":
                user_exists_kwargs[username_field] = admin_email

            if User.objects.filter(**user_exists_kwargs).exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"Superuser with email '{admin_email}' (or corresponding username) already exists. Skipping creation."
                    )
                )
            else:
                try:
                    create_kwargs = {
                        username_field: admin_email,
                        "email": admin_email,
                        "password": admin_password,
                    }
                    User.objects.create_superuser(**create_kwargs)
                    self.stdout.write(
                        self.style.SUCCESS(f"Superuser '{admin_email}' created successfully.")
                    )
                except IntegrityError as e:
                    self.stdout.write(
                        self.style.ERROR(f"Failed to create superuser '{admin_email}'. IntegrityError: {e}")
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(
                            f"An unexpected error occurred creating superuser '{admin_email}': {e}"
                        )
                    )
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "ADMIN_EMAIL or ADMIN_PASSWORD environment variables not set. Skipping superuser creation."
                )
            )

        self.stdout.write("---")

        # --- Create Demo User ---
        create_demo_user = getattr(settings, "DEMO", False)

        if create_demo_user:
            demo_email = "demo@demo.com"
            demo_password = "argusdemo"
            demo_username = demo_email

            self.stdout.write(
                f"DEMO setting is True. Attempting to create demo user: {demo_email}"
            )

            username_field = User.USERNAME_FIELD

            user_exists_kwargs = {"email": demo_email}
            if username_field != "email":
                user_exists_kwargs[username_field] = demo_username

            if User.objects.filter(**user_exists_kwargs).exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"Demo user with email '{demo_email}' already exists. Skipping creation."
                    )
                )
            else:
                try:
                    create_kwargs = {
                        username_field: demo_username,
                        "email": demo_email,
                        "password": demo_password,
                    }
                    User.objects.create_user(**create_kwargs)
                    self.stdout.write(
                        self.style.SUCCESS(f"Demo user '{demo_email}' created successfully.")
                    )
                except IntegrityError as e:
                    self.stdout.write(
                        self.style.ERROR(f"Failed to create demo user '{demo_email}'. IntegrityError: {e}")
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(
                            f"An unexpected error occurred creating demo user '{demo_email}': {e}"
                        )
                    )
        else:
            self.stdout.write(
                self.style.NOTICE("DEMO setting is not True (or not set). Skipping demo user creation.")
            )

        self.stdout.write(self.style.SUCCESS("User setup command finished."))
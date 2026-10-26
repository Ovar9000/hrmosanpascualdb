from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hr"
    verbose_name = "Human Resources"

    def ready(self):
        import hr.signals  # noqa: F401
        from django.utils.autoreload import autoreload_started

        autoreload_started.connect(_watch_release_file)


def _watch_release_file(sender, **kwargs):
    """runserver restarts when RELEASE changes (the git hooks rewrite it), so the footer follows (H9)."""
    from django.conf import settings

    sender.watch_dir(settings.BASE_DIR, "RELEASE")


"""Test runner for the suite (settings.TEST_RUNNER)."""

from django.conf import settings
from django.test.runner import DiscoverRunner


class Runner(DiscoverRunner):
    """Turns the required second login step off for the suite (DECISIONS A5).

    Hundreds of tests log in as HRMO, Auditor or System Admin with force_login; making each of them
    pass a code would test the second step a few hundred times over. MfaTestCase turns it back on
    with override_settings, and an account that has an app must always pass it regardless.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.MFA_REQUIRED = False

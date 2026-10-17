"""Template context shared by every page."""

from django.utils.functional import SimpleLazyObject
from django.utils import timezone
from .models import SalaryAction
from .meetings import needs_attention as meetings_need_attention
from .reminders import needs_attention
from .permissions import can_view_office_tree, can_view_salary_actions, get_user_office


def org_chart_nav(request):
    """Whether to show the Org Chart and My Office menu links. Lazy: costs nothing on pages
    that never render the menu (DECISIONS O3)."""

    def links():
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return {"tree": False, "my_office": False, "salary": False}
        salary = can_view_salary_actions(user)
        return {
            "tree": can_view_office_tree(user),
            "my_office": get_user_office(user) is not None,
            "salary": salary,
            "salary_due": salary and SalaryAction.objects.filter(status=SalaryAction.Status.DUE).exists(),
            "attention": salary and (
                needs_attention(timezone.localdate()) or meetings_need_attention(timezone.localdate())
            ),
        }

    return {"org_nav": SimpleLazyObject(links)}

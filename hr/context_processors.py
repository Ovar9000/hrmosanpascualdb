"""Template context shared by every page."""

from django.utils.functional import SimpleLazyObject
from .models import SalaryAction
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
            "salary_due": SalaryAction.objects.filter(status=SalaryAction.Status.DUE).count() if salary else 0,
        }

    return {"org_nav": SimpleLazyObject(links)}

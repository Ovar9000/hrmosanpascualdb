"""Template context shared by every page."""

from django.utils.functional import SimpleLazyObject
from .permissions import can_view_office_tree, get_user_office


def org_chart_nav(request):
    """Whether to show the Org Chart and My Office menu links. Lazy: costs nothing on pages
    that never render the menu (DECISIONS O3)."""

    def links():
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return {"tree": False, "my_office": False}
        return {"tree": can_view_office_tree(user), "my_office": get_user_office(user) is not None}

    return {"org_nav": SimpleLazyObject(links)}

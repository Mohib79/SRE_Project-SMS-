from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

from .models import UserRole

# Which user_type is allowed to reach views defined in each module.
MODULE_ROLES = {
    "main_app.hod_views": UserRole.HOD,
    "main_app.staff_views": UserRole.STAFF,
    "main_app.student_views": UserRole.STUDENT,
}

# Modules whose views are reachable by any authenticated user. Views in these
# modules MUST perform their own role / ownership checks.
SHARED_MODULES = {
    "main_app.views",
}


class LoginCheckMiddleWare(MiddlewareMixin):
    """Enforces authentication and module-level role authorization per request."""

    def _is_public_auth_request(self, request, modulename):
        is_login_page = request.path == reverse("login_page")
        is_auth_view = modulename == "django.contrib.auth.views"
        is_login_submit = request.path == reverse("user_login")
        return is_login_page or is_auth_view or is_login_submit

    def _authorize_authenticated_user(self, request, modulename):
        user = request.user
        home = user.get_dashboard_url_name()
        if home is None:
            logout(request)
            return redirect(reverse("login_page"))

        required_role = MODULE_ROLES.get(modulename)
        if required_role is not None:
            if UserRole.normalize(user.user_type) != required_role:
                return redirect(reverse(home))
            return None

        if modulename.startswith("main_app.") and modulename not in SHARED_MODULES:
            return redirect(reverse(home))
        return None

    def process_view(self, request, view_func, view_args, view_kwargs):
        modulename = view_func.__module__
        if request.user.is_authenticated:
            return self._authorize_authenticated_user(request, modulename)
        if not self._is_public_auth_request(request, modulename):
            return redirect(reverse("login_page"))
        return None

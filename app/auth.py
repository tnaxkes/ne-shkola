from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import settings

ADMIN_SESSION_KEY = "admin_authenticated"


def is_authenticated(request: Request) -> bool:
    return request.session.get(ADMIN_SESSION_KEY) is True


def require_auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    return None


def login(request: Request, username: str, password: str) -> bool:
    if username == settings.admin_username and password == settings.admin_password:
        request.session[ADMIN_SESSION_KEY] = True
        return True
    return False


def logout(request: Request):
    request.session.pop(ADMIN_SESSION_KEY, None)

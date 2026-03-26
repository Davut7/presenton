import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from utils.get_env import get_can_change_keys_env
from utils.user_config import update_env_with_user_config


class UserConfigEnvUpdateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if get_can_change_keys_env() != "false":
            update_env_with_user_config()
        return await call_next(request)


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Validates API key from X-API-Key header or api_key query param."""

    async def dispatch(self, request: Request, call_next):
        api_key = os.getenv("API_KEY")
        if not api_key:
            # No API_KEY set — auth disabled, allow all
            return await call_next(request)

        # Allow truly internal requests (no X-Forwarded-For = not proxied through nginx from outside)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if not forwarded_for:
            # Direct internal call (Next.js, Puppeteer) — not from nginx
            return await call_next(request)

        # External request through nginx — require API key
        request_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if request_key != api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)

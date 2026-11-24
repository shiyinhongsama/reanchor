"""单用户令牌鉴权：HMAC-SHA256 签名，无状态、无外部依赖。

令牌格式：``<expiry_unix>.<hex_signature>``，签名为 HMAC(secret, str(expiry))。
校验只验签名与有效期，不查库——重启后旧令牌仍有效（除非换了 secret_key）。

保护范围：/api/v1 下除 /api/v1/auth/login 外的所有端点；/api/health 保持公开。
"""
from __future__ import annotations

import hashlib
import hmac
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import get_settings

PUBLIC_PREFIXES = ("/api/v1/auth/login", "/api/health", "/docs", "/openapi.json")


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(ttl_hours: int | None = None) -> tuple[str, int]:
    """签发令牌，返回 (token, expiry_unix)。"""
    s = get_settings()
    expiry = int(time.time()) + int((ttl_hours or s.token_ttl_hours) * 3600)
    sig = _sign(str(expiry), s.secret_key)
    return f"{expiry}.{sig}", expiry


def verify_token(token: str) -> bool:
    """校验令牌签名与有效期（恒定时间比较，防时序攻击）。"""
    try:
        expiry_str, sig = token.split(".", 1)
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        return False
    if expiry < int(time.time()):
        return False
    expected = _sign(expiry_str, get_settings().secret_key)
    return hmac.compare_digest(sig, expected)


def check_password(password: str) -> bool:
    return hmac.compare_digest(password, get_settings().admin_password)


class AuthMiddleware(BaseHTTPMiddleware):
    """/api/v1 保护：无有效 Bearer 令牌一律 401（公开路径除外）。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in PUBLIC_PREFIXES) or not path.startswith("/api/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token or not verify_token(token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

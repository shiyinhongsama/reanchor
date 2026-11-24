"""登录接口。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.auth import check_password, issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: int          # unix 秒


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    if not check_password(req.password):
        raise HTTPException(401, "密码错误")
    token, expiry = issue_token()
    return LoginResponse(token=token, expires_at=expiry)


@router.get("/me")
async def me():
    """令牌探活：能走到这说明中间件已放行。"""
    return {"ok": True}

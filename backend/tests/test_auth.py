"""鉴权测试：令牌签发/校验 + 中间件保护范围（内存 app，无外网）。

运行：PYTHONPATH=. python tests/test_auth.py
"""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import AuthMiddleware, check_password, issue_token, verify_token
from app.core.config import get_settings


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/health")
    def health(): return {"ok": True}

    @app.get("/api/v1/executors")
    def executors(): return {"ok": True}

    @app.post("/api/v1/auth/login")
    def login(): return {"ok": True}

    return app


def test_token_roundtrip():
    token, expiry = issue_token(ttl_hours=1)
    assert verify_token(token), "新签发的令牌应有效"
    assert expiry > int(time.time())
    # 篡改签名 / 篡改过期时间都应失败
    assert not verify_token(token[:-1] + ("0" if token[-1] != "0" else "1"))
    fake = f"{expiry + 3600}.deadbeef"
    assert not verify_token(fake)
    # 过期令牌
    past, _ = issue_token(ttl_hours=1)
    past = f"{int(time.time()) - 10}.{past.split('.', 1)[1]}"
    assert not verify_token(past)
    # 垃圾输入
    assert not verify_token("") and not verify_token("abc") and not verify_token("1.2.3")
    # 密码校验
    assert check_password(get_settings().admin_password)
    assert not check_password("wrong")
    print("token tests passed")


def test_middleware():
    client = TestClient(_app())
    token, _ = issue_token(ttl_hours=1)

    assert client.get("/api/health").status_code == 200          # 公开
    assert client.post("/api/v1/auth/login").status_code == 200  # 公开
    assert client.get("/api/v1/executors").status_code == 401    # 无令牌
    assert client.get("/api/v1/executors",
                      headers={"Authorization": "Bearer garbage"}).status_code == 401
    r = client.get("/api/v1/executors", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200                                   # 有效令牌放行
    print("middleware tests passed")


if __name__ == "__main__":
    test_token_roundtrip()
    test_middleware()

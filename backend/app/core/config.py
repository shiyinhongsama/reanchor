"""全局配置：环境变量优先，默认值供本地开发。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TP_", env_file=".env", extra="ignore")

    app_name: str = "reanchor"
    env: str = "dev"                      # dev | prod
    database_url: str = "sqlite+aiosqlite:///./trading.db"
    redis_url: str = "redis://localhost:6379/0"

    # 交易所（一期 Gate.io，经 ccxt；行情无需 key）
    exchange: str = "gate"
    gate_api_key: str = ""
    gate_api_secret: str = ""
    gate_sandbox: bool = False

    # 回测默认成本假设（与研究一致）
    default_fee: float = 0.0005
    default_slippage: float = 0.0002
    default_funding_per_8h: float = 0.0001

    # 实盘安全阈值
    live_max_leverage: float = 3.0
    live_enabled: bool = False            # 实盘总开关，默认关闭

    # 登录鉴权（单用户；生产部署务必改密码与密钥）
    admin_password: str = "reanchor"
    secret_key: str = "reanchor-dev-secret-change-me"
    token_ttl_hours: int = 168            # 令牌有效期，默认 7 天


@lru_cache
def get_settings() -> Settings:
    return Settings()

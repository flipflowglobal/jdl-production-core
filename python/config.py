import os
from pathlib import Path

REQUIRED_PROD = ["PRIVATE_KEY", "ALCHEMY_ARB_KEY"]

def validate_env():
    env = os.getenv("ENVIRONMENT", "development")
    if env == "production":
        for key in REQUIRED_PROD:
            if not os.getenv(key):
                raise ValueError(f"FATAL: {key} not set in production")

CONFIG = {
    "private_key": os.getenv("PRIVATE_KEY", ""),
    "wallet": os.getenv("WALLET_ADDRESS", ""),
    "dry_run": os.getenv("DRY_RUN", "false").lower() == "true",
    "db_path": Path(os.getenv("DB_PATH", "~/.jdl_prod")).expanduser(),
}

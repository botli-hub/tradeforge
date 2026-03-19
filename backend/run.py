#!/usr/bin/env python3
"""TradeForge 后端启动入口"""

import uvicorn
from app.core.config import load_local_env
from app.main import app


load_local_env()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )

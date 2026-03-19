from app.core.config import get_settings

print(bool(get_settings()["finnhub_api_key"]))

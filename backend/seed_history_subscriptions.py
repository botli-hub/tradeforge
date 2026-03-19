from app.data.database import init_db
from app.data.history_backfill import resolve_history_source
from app.data.history_repository import upsert_subscription

symbols = [
    'AAPL', 'QQQ', 'TSLA', 'GOOGL', 'NVDA', 'AMD',
    '600519.SH', '300750.SZ', '00700.HK', '00883.HK'
]

init_db()
for symbol in symbols:
    upsert_subscription(symbol, name=symbol, source_hint=resolve_history_source(symbol), enabled=True)
    print('subscribed', symbol, resolve_history_source(symbol))

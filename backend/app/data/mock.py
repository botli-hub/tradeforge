"""Mock K线数据生成器 - 几何布朗运动"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict

def generate_klines(
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_price: float = 100.0,
    volatility: float = 0.02,
    drift: float = 0.0001
) -> List[Dict]:
    """
    生成模拟K线数据（几何布朗运动）
    
    Args:
        symbol: 股票代码
        timeframe: 时间周期 (1m/5m/15m/1h/1d)
        start_date: 开始日期
        end_date: 结束日期
        initial_price: 初始价格
        volatility: 波动率
        drift: 漂移率
    
    Returns:
        K线数据列表
    """
    # 解析时间
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    
    # 根据周期计算数据点数量
    timeframe_minutes = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
        "1w": 10080
    }
    
    period_min = timeframe_minutes.get(timeframe, 1440)
    total_minutes = int((end - start).total_seconds() / 60)
    n = total_minutes // period_min
    
    if n > 5000:
        n = 5000  # 限制最大数据点
    
    # 生成价格路径（几何布朗运动）
    dt = period_min / 1440  # 转换为天
    random_shocks = np.random.normal(0, 1, n)
    log_returns = drift * dt + volatility * np.sqrt(dt) * random_shocks
    log_prices = np.log(initial_price) + np.cumsum(log_returns)
    prices = np.exp(log_prices)
    
    # 生成K线数据
    klines = []
    current_time = start
    
    for i in range(n):
        open_price = prices[i] if i == 0 else prices[i-1]
        close_price = prices[i]
        
        # 生成高低价
        daily_vol = volatility * open_price * 0.5
        high_price = max(open_price, close_price) + np.random.uniform(0, daily_vol)
        low_price = min(open_price, close_price) - np.random.uniform(0, daily_vol)
        
        # 生成成交量
        volume = np.random.uniform(1000000, 10000000)
        
        klines.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": current_time.isoformat(),
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
            "volume": int(volume)
        })
        
        # 前进时间
        current_time += timedelta(minutes=period_min)
    
    return klines


# 预设热门股票信息
STOCK_INFO = {
    "AAPL": {"name": "Apple Inc.", "base_price": 185.0},
    "TSLA": {"name": "Tesla Inc.", "base_price": 245.0},
    "NVDA": {"name": "NVIDIA Corp.", "base_price": 520.0},
    "MSFT": {"name": "Microsoft Corp.", "base_price": 380.0},
    "GOOGL": {"name": "Alphabet Inc.", "base_price": 140.0},
    "AMZN": {"name": "Amazon.com Inc.", "base_price": 155.0},
    "META": {"name": "Meta Platforms", "base_price": 380.0},
    "SPY": {"name": "SPDR S&P 500 ETF", "base_price": 450.0},
    "QQQ": {"name": "Invesco QQQ Trust", "base_price": 380.0},
    "600519.SH": {"name": "贵州茅台", "base_price": 1680.0},
    "600900.SH": {"name": "长江电力", "base_price": 27.5},
    "302132.SZ": {"name": "中航成飞", "base_price": 73.0},
    "000001.SZ": {"name": "平安银行", "base_price": 11.2},
    "601318.SH": {"name": "中国平安", "base_price": 52.0},
    "00700.HK": {"name": "腾讯控股", "base_price": 550.0},
    "00941.HK": {"name": "中国移动", "base_price": 77.5},
    "01810.HK": {"name": "小米集团-W", "base_price": 21.8},
    "300750.SZ": {"name": "宁德时代", "base_price": 255.0},
    "00883.HK": {"name": "中国海洋石油", "base_price": 21.0},
    "AMD": {"name": "Advanced Micro Devices", "base_price": 165.0},
}

def get_stock_info(symbol: str) -> Dict:
    """获取股票基本信息"""
    return STOCK_INFO.get(symbol.upper(), {"name": symbol, "base_price": 100.0})

def search_stocks(keyword: str) -> List[Dict]:
    """搜索股票"""
    keyword = keyword.upper()
    results = []
    for code, info in STOCK_INFO.items():
        if keyword in code or keyword in info["name"].upper():
            results.append({
                "symbol": code,
                "name": info["name"],
                "price": info["base_price"]
            })
    return results

"""期权API（固定走 Futu）"""
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.data.adapter import get_adapter
from app.data.options import calculate_payoff

router = APIRouter()


class PayoffRequest(BaseModel):
    strategy: str
    underlying_price: float
    legs: List[Dict[str, Any]]


def _normalize_futu_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.startswith(('US.', 'HK.', 'SH.', 'SZ.')):
        return symbol
    if symbol.endswith('.HK'):
        return f"HK.{symbol[:-3].zfill(5)}"
    if symbol.endswith('.SH'):
        return f"SH.{symbol[:-3]}"
    if symbol.endswith('.SZ'):
        return f"SZ.{symbol[:-3]}"
    if symbol.isdigit():
        if len(symbol) == 6:
            return f"SH.{symbol}" if symbol.startswith(('5', '6', '9')) else f"SZ.{symbol}"
        return f"HK.{symbol.zfill(5)}"
    return f"US.{symbol}"


def _display_symbol(futu_symbol: str) -> str:
    futu_symbol = futu_symbol.strip().upper()
    if futu_symbol.startswith('HK.'):
        return f"{futu_symbol.split('.', 1)[1]}.HK"
    if futu_symbol.startswith('SH.'):
        return f"{futu_symbol.split('.', 1)[1]}.SH"
    if futu_symbol.startswith('SZ.'):
        return f"{futu_symbol.split('.', 1)[1]}.SZ"
    if futu_symbol.startswith('US.'):
        return futu_symbol.split('.', 1)[1]
    return futu_symbol


def _normalize_iv(value: Any) -> float:
    try:
        iv = float(value or 0)
    except Exception:
        return 0.0
    if iv > 10:
        return round(iv / 1000, 6)
    if iv > 3:
        return round(iv / 100, 6)
    return round(iv, 6)


def _chunk(items: List[str], size: int = 80) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _get_underlying_spot(symbol: str, host: str, port: int):
    display_symbol = _display_symbol(symbol)
    market_adapter = None
    try:
        market_adapter = get_adapter(adapter_type='futu', host=host, port=port)
        if hasattr(market_adapter, 'connect') and not market_adapter.connect():
            raise RuntimeError(getattr(market_adapter, 'last_error', None) or '连接 Futu 行情源失败')

        quote = market_adapter.get_quote(display_symbol) if hasattr(market_adapter, 'get_quote') else None
        if quote is not None and getattr(quote, 'price', 0):
            return float(quote.price), getattr(quote, 'name', display_symbol), 'futu_quote', getattr(market_adapter, 'last_error', None)
    except Exception:
        pass
    finally:
        if market_adapter and hasattr(market_adapter, 'disconnect'):
            try:
                market_adapter.disconnect()
            except Exception:
                pass

    raise HTTPException(
        status_code=502,
        detail=f'无法获取 {display_symbol} 实时报价，请确认富途 OpenD 已启动并连接正常。'
    )


def _load_option_expirations(symbol: str, host: str, port: int) -> List[str]:
    try:
        from futu import OpenQuoteContext, RET_OK
        ctx = OpenQuoteContext(host=host, port=port)
        try:
            ret, data = ctx.get_option_expiration_date(_normalize_futu_symbol(symbol))
            if ret != RET_OK:
                raise HTTPException(status_code=502, detail=str(data))
            expirations = []
            for _, row in data.iterrows():
                expirations.append(str(row['strike_time']))
            return expirations
        finally:
            ctx.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'获取 Futu 期权到期日失败: {e}')


def _load_option_chain(symbol: str, expiry: str, host: str, port: int) -> Dict[str, Any]:
    normalized = _normalize_futu_symbol(symbol)
    display = _display_symbol(normalized)
    spot_price, name, pricing_source, detail = _get_underlying_spot(normalized, host, port)

    try:
        from futu import OpenQuoteContext, RET_OK
        ctx = OpenQuoteContext(host=host, port=port)
        try:
            ret, chain_df = ctx.get_option_chain(normalized, start=expiry, end=expiry)
            if ret != RET_OK or len(chain_df) == 0:
                raise HTTPException(status_code=502, detail=f'获取 Futu 期权链失败: {chain_df}')

            codes = chain_df['code'].tolist()
            snapshots: Dict[str, Dict[str, Any]] = {}
            for code_batch in _chunk(codes, 80):
                ret_snap, snap_df = ctx.get_market_snapshot(code_batch)
                if ret_snap != RET_OK:
                    raise HTTPException(status_code=502, detail=f'获取 Futu 期权快照失败: {snap_df}')
                for _, row in snap_df.iterrows():
                    snapshots[str(row['code'])] = row.to_dict()
        finally:
            ctx.close()

        contracts: List[Dict[str, Any]] = []
        for _, row in chain_df.iterrows():
            code = str(row['code'])
            snap = snapshots.get(code, {})
            strike = float(snap.get('option_strike_price') or row.get('strike_price') or 0)
            option_type = str(snap.get('option_type') or row.get('option_type') or '').upper()
            last = float(snap.get('last_price') or 0)
            bid = float(snap.get('bid_price') or last or 0)
            ask = float(snap.get('ask_price') or last or 0)
            intrinsic = max(spot_price - strike, 0.0) if option_type == 'CALL' else max(strike - spot_price, 0.0)
            time_value = max(last - intrinsic, 0.0)

            contracts.append({
                'option_symbol': code,
                'underlying_symbol': display,
                'expiry': str(snap.get('strike_time') or row.get('strike_time') or expiry),
                'strike': strike,
                'option_type': option_type,
                'bid': bid,
                'ask': ask,
                'last': last,
                'iv': _normalize_iv(snap.get('option_implied_volatility')),
                'delta': round(float(snap.get('option_delta') or 0), 6),
                'gamma': round(float(snap.get('option_gamma') or 0), 6),
                'theta': round(float(snap.get('option_theta') or 0), 6),
                'vega': round(float(snap.get('option_vega') or 0), 6),
                'volume': int(float(snap.get('volume') or 0)),
                'open_interest': int(float(snap.get('option_open_interest') or 0)),
                'intrinsic_value': round(intrinsic, 2),
                'time_value': round(time_value, 2),
                'contract_size': int(float(snap.get('option_contract_size') or row.get('lot_size') or 100)),
            })

        return {
            'symbol': display,
            'name': name,
            'expiry': expiry,
            'spot_price': round(spot_price, 2),
            'days_to_expiry': max((date.fromisoformat(expiry) - date.today()).days, 0),
            'contracts': contracts,
            'adapter': 'futu',
            'pricing_source': 'futu',
            'detail': detail,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'Futu 期权链异常: {e}')


@router.get('/expirations')
async def get_option_expirations(
    symbol: str = Query(..., description='标的代码'),
    host: str = Query('127.0.0.1', description='OpenD 地址'),
    port: int = Query(11111, description='OpenD 端口'),
):
    expirations = _load_option_expirations(symbol, host, port)
    return {
        'symbol': _display_symbol(_normalize_futu_symbol(symbol)),
        'expirations': expirations,
        'adapter': 'futu',
    }


@router.get('/chain')
async def get_option_chain(
    symbol: str = Query(..., description='标的代码'),
    expiry: Optional[str] = Query(None, description='到期日 YYYY-MM-DD'),
    host: str = Query('127.0.0.1', description='OpenD 地址'),
    port: int = Query(11111, description='OpenD 端口'),
):
    expirations = _load_option_expirations(symbol, host, port)
    if not expirations:
        raise HTTPException(status_code=502, detail='未获取到可用期权到期日')
    target_expiry = expiry or expirations[0]
    return _load_option_chain(symbol, target_expiry, host, port)


@router.post('/payoff')
async def get_option_payoff(req: PayoffRequest):
    if not req.legs:
        raise HTTPException(status_code=400, detail='至少需要一条期权腿')
    return calculate_payoff(req.strategy, req.underlying_price, req.legs)

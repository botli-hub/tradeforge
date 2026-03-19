from futu import OpenQuoteContext

ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret1, exp_df = ctx.get_option_expiration_date('US.QQQ')
if ret1 != 0 or len(exp_df) == 0:
    print('expiration error', exp_df)
    ctx.close()
    raise SystemExit(1)

row = exp_df[exp_df['option_expiry_date_distance'] >= 1].head(1)
if len(row) == 0:
    row = exp_df.head(1)
expiry = row.iloc[0]['strike_time']
print('expiry=', expiry)

ret2, chain_df = ctx.get_option_chain('US.QQQ', start=expiry, end=expiry)
if ret2 != 0 or len(chain_df) == 0:
    print('chain error', chain_df)
    ctx.close()
    raise SystemExit(1)

codes = chain_df['code'].head(4).tolist()
print('codes=', codes)
ret3, snap_df = ctx.get_market_snapshot(codes)
print('snapshot_ret=', ret3)
if ret3 == 0:
    cols = [c for c in ['code', 'name', 'last_price', 'bid_price', 'ask_price', 'volume', 'option_delta', 'option_gamma', 'option_theta', 'option_vega', 'option_implied_volatility'] if c in snap_df.columns]
    print(snap_df[cols].to_string())
else:
    print(snap_df)

ctx.close()

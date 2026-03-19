from futu import OpenQuoteContext

ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret1, exp_df = ctx.get_option_expiration_date('US.QQQ')
print('exp_ret', ret1)
print('exp_cols', list(exp_df.columns) if ret1 == 0 else exp_df)
expiry = exp_df[exp_df['option_expiry_date_distance'] >= 1].head(1).iloc[0]['strike_time'] if ret1 == 0 and len(exp_df) > 0 else None
print('expiry', expiry)
if expiry:
    ret2, chain_df = ctx.get_option_chain('US.QQQ', start=expiry, end=expiry)
    print('chain_ret', ret2)
    if ret2 == 0:
        print('chain_cols', list(chain_df.columns))
        print(chain_df.head(3).to_string())
        codes = chain_df['code'].head(2).tolist()
        ret3, snap_df = ctx.get_market_snapshot(codes)
        print('snap_ret', ret3)
        if ret3 == 0:
            print('snap_cols', list(snap_df.columns))
            print(snap_df.head(2).to_string())
        else:
            print(snap_df)
ctx.close()

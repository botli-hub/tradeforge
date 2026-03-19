from inspect import signature
from futu import OpenQuoteContext

ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
print('get_option_expiration_date', signature(ctx.get_option_expiration_date))
print('get_option_chain', signature(ctx.get_option_chain))

ret1, data1 = ctx.get_option_expiration_date('US.QQQ')
print('expiration_ret=', ret1)
print(data1 if ret1 != 0 else data1.head().to_string())

if ret1 == 0 and len(data1) > 0:
    expiry = data1.iloc[0]['strike_time'] if 'strike_time' in data1.columns else data1.iloc[0][0]
    print('using expiry=', expiry)
    ret2, data2 = ctx.get_option_chain('US.QQQ', start=expiry, end=expiry)
    print('chain_ret=', ret2)
    if ret2 == 0:
        print(data2.head().to_string())
    else:
        print(data2)

ctx.close()

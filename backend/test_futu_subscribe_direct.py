from futu import OpenQuoteContext, SubType, RET_OK

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

for code in ['US.AAPL', 'HK.00700']:
    ret_sub, result = quote_ctx.subscribe([code], [SubType.QUOTE], subscribe_push=False)
    print('SUBSCRIBE', code, ret_sub, result if ret_sub != RET_OK else 'OK')
    if ret_sub == RET_OK:
        ret_quote, data = quote_ctx.get_stock_quote([code])
        if ret_quote == RET_OK:
            print('QUOTE', code, data[['code', 'name', 'last_price']].to_dict('records'))
        else:
            print('QUOTE', code, 'ERROR', data)

quote_ctx.close()

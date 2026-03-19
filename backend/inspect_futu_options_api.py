from futu import OpenQuoteContext

ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
methods = [name for name in dir(ctx) if 'option' in name.lower()]
for name in sorted(methods):
    print(name)
ctx.close()

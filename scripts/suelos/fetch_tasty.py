import asyncio, sys, yaml, pickle, time
from datetime import datetime, timedelta
from tastytrade import Session
from tastytrade.dxfeed import Candle
from tastytrade.streamer import DXLinkStreamer
cfg=yaml.safe_load(open('C:/Users/marlo/ProyectosVSC/ScannerOpcionesApp/config/config.yaml',encoding='utf-8'))['tastytrade']
syms=sys.argv[2].split(','); years=int(sys.argv[3]); out=sys.argv[1]
async def main():
    s=Session(cfg['client_secret'],cfg['refresh_token'])
    res={}
    async with DXLinkStreamer(s) as st:
        for i in range(0,len(syms),10):
            batch=syms[i:i+10]; pend=set(batch); bars={x:{} for x in batch}
            await st.subscribe_candle(batch,'1d',start_time=datetime.now()-timedelta(days=365*years))
            async def rd():
                async for c in st.listen(Candle):
                    sym=c.event_symbol.split('{')[0]
                    if sym not in bars: continue
                    if c.close and float(c.close)>0:
                        bars[sym][c.time]=(c.open,c.high,c.low,c.close,c.volume,c.vwap)
                    if c.snapshot_end:
                        pend.discard(sym)
                        if not pend: return
            try: await asyncio.wait_for(rd(),timeout=120)
            except asyncio.TimeoutError: print('timeout',sorted(pend))
            for x in batch:
                await st.unsubscribe_candle(x,'1d'); res[x]=bars[x]
                b=sorted(bars[x].items()); print(x,len(b),datetime.fromtimestamp(b[0][0]/1000).date() if b else None,b[-1][1] if b else None,flush=True)
    pickle.dump(res,open(out,'wb'))
asyncio.run(main())

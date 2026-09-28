"""Opt-in periodic archive of enabled target expiries; never sends alerts."""
import logging
import time
import threading
from app.data import wheel_research_repository as store

logger=logging.getLogger(__name__)
_LOCK=threading.Lock()


def capture_once():
    from app.core.config import get_effective_config
    from app.data.wheel_repository import get_targets
    from app.services.wheel_scanner import cached_expirations,cached_chain
    if not _LOCK.acquire(False):raise ValueError('行情留档正在进行')
    try:
        cfg=get_effective_config();futu=cfg.get('futu') or {};host=futu.get('host','127.0.0.1');port=futu.get('port',11111)
        results=[]
        for t in get_targets():
            if not t.get('enabled'):continue
            try:
                # Full available expiry list, not only signal winners. Explicitly bounded
                # by provider availability, with failures recorded per expiry.
                expiries=cached_expirations(t['symbol'],host,port,force=True)
                for exp in expiries:
                    try:
                        chain=cached_chain(t['symbol'],exp,host,port,force=True)
                        results.append({'symbol':t['symbol'],'expiry':exp,'snapshot_id':chain.get('research_snapshot_id')})
                    except Exception as e:results.append({'symbol':t['symbol'],'expiry':exp,'error':str(e)})
                    time.sleep(3.2)
            except Exception as e:results.append({'symbol':t['symbol'],'error':str(e)})
        from app.core.wheel_nav import compute_account_nav
        store.observe_nav(compute_account_nav(float((cfg.get('wheel_portfolio') or {}).get('total_equity') or 0)))
        event_id=store.append_event('capture_run' ,{'items':results,'coverage':'currently_available_expiries'})
        return {'ok':not any(r.get('error') for r in results),'items':results,'research_event_id':event_id}
    finally:_LOCK.release()


def archive_loop():
    last=0.
    while True:
        try:
            from app.core.config import get_effective_config
            interval=int(get_effective_config().get('wheel_research',{}).get('archive_interval_minutes',0))
            if interval>=15 and time.monotonic()-last>=interval*60:
                capture_once();last=time.monotonic()
        except Exception as e:
            logger.warning('research archive failed: %s',e);last=time.monotonic()
        time.sleep(30)

_STATUS_LOCK = threading.Lock()
_STATUS = {'running': False, 'result': None, 'error': None}


def capture_status():
    with _STATUS_LOCK:
        return dict(_STATUS)


def start_capture():
    with _STATUS_LOCK:
        if _STATUS['running'] or _LOCK.locked():
            raise ValueError('行情留档正在进行')
        _STATUS.update(running=True, result=None, error=None)
    def work():
        try:
            result=capture_once()
            with _STATUS_LOCK:
                _STATUS['result']=result
        except Exception as e:
            with _STATUS_LOCK:
                _STATUS['error']=str(e)
        finally:
            with _STATUS_LOCK:
                _STATUS['running']=False
    threading.Thread(target=work,daemon=True).start()
    return {'running':True}

"""Services package. 持仓告警周期薄接线事件日首次入窗推送."""


def _install_event_alert_hook() -> None:
    """替换 run_position_alert_cycle:同一轮体检后追加事件日 Telegram(首次入窗指纹).

    不改 alert_engine 正文,避免整文件重写. from app.services.alert_engine import ...
    会先加载本包,因此拿到的是挂钩后的函数. 失败静默,不影响原推送.
    """
    try:
        import app.services.alert_engine as ae
        if getattr(ae, "_event_window_hook", False):
            return

        def run_position_alert_cycle(
            host: str = "127.0.0.1",
            port: int = 11111,
            *,
            force: bool = False,
            dry_run: bool = False,
        ):
            from app.api.wheel import check_open_positions_core
            from app.api.leaps import _load_config

            cfg = _load_config()
            data = check_open_positions_core(host, port)
            items = data.get("items") or []
            out = ae.process_position_alerts(items, cfg=cfg, force=force, dry_run=dry_run)
            out["checked"] = len(items)
            out["profit_target_pct"] = data.get("profit_target_pct")
            try:
                from app.core.wheel_event_dispose import process_event_window_alerts
                ev = process_event_window_alerts(
                    items, cfg=cfg, force=force, dry_run=dry_run,
                )
                out["event_alerts"] = ev
                extra = int(ev.get("sent_count") or 0)
                if extra:
                    out["sent_count"] = int(out.get("sent_count") or 0) + extra
            except Exception:
                pass
            return out

        ae.run_position_alert_cycle = run_position_alert_cycle  # type: ignore[assignment]
        ae._event_window_hook = True
    except Exception:
        pass


_install_event_alert_hook()

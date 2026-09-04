"""Services package. 持仓告警周期薄接线事件日首次入窗推送 + Telegram 分频道."""


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


def _install_telegram_channel_hook() -> None:
    """替换 send_and_log:按 channel_kind 走分频道 Bot;缺键静默不回落全局 bot.

    不改 alert_engine 正文。category==chan 默认 telegram.chan;
    显式 channel_kind=timing_put/timing_call 亦可。持仓等仍走 legacy。
    """
    try:
        import app.services.alert_engine as ae
        if getattr(ae, "_telegram_channel_hook", False):
            return

        def send_and_log(
            text: str,
            *,
            category: str,
            fingerprint: str = "",
            title: str = "",
            meta=None,
            dry_run: bool = False,
            cfg=None,
            channel_kind=None,
        ):
            if cfg is None:
                try:
                    from app.api.leaps import _load_config
                    cfg = _load_config()
                except Exception:
                    cfg = {}
            from app.services.telegram_channel_send import send_telegram_logged
            return send_telegram_logged(
                text,
                category=category,
                fingerprint=fingerprint,
                title=title,
                meta=meta,
                dry_run=dry_run,
                cfg=cfg,
                channel_kind=channel_kind,
                log_push_fn=ae.log_push,
            )

        ae.send_and_log = send_and_log  # type: ignore[assignment]
        ae._telegram_channel_hook = True
    except Exception:
        pass


_install_event_alert_hook()
_install_telegram_channel_hook()

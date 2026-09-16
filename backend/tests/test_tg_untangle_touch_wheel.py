"""TG 推送不套 touch_wheel prefer_daily；Sim 才用同批规则。"""
from pathlib import Path


def test_leaps_run_wheel_scan_keeps_tg_on_select_best_only():
    src = Path(__file__).resolve().parents[1] / "app" / "api" / "leaps.py"
    # In CI/repo layout tests live under backend/tests → parents[1]=backend
    if not src.exists():
        src = Path("backend/app/api/leaps.py")
    text = src.read_text(encoding="utf-8")
    assert "select_best_touch_signals(signals)" in text
    # prefer_daily / select_touch_batch 只应出现在 Sim 分支注释/调用里，且 TG 循环前先 best
    assert "TG: 只做旧择优" in text or "按 symbol+side 年化/theta" in text
    assert "select_touch_batch_for_push" in text
    # 确保 TG send 循环用的是 push_signals（best），不是 sim_signals
    assert "for sig in push_signals:" in text
    assert "for sig in sim_signals:" in text

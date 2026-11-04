# -*- coding: utf-8 -*-
"""数据护栏（ACCOUNT-GATE 阶段0）的单元测试。

场景全部对着真实事故写（见 agent/data_guards.py 模块头）：
- 2026-10-14 10:12:12：半根柱进信号计算 → drop_forming_bar / 完结口径
- 2026-10-12 09:27:04：盘前竞价柱被结算 → expected=0 时当日柱不可结算
- 数据源卡住/停牌：旧行情照常结算 → stale_data
- 断档被当成"没有信号" → data_gap
"""
from datetime import datetime, timedelta

from agent import data_guards


def bar(label, close="7.00"):
    return {"date": label, "open": close, "close": close, "high": close, "low": close,
            "volume": "1000"}


def minute_labels(start: datetime, count: int):
    return [(start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S") for i in range(count)]


DAY = datetime(2026, 11, 4, 9, 30)


class TestExpectedBarsToday:
    def test_pre_open_is_zero(self):
        assert data_guards.expected_bars_today(datetime(2026, 11, 4, 9, 30, 59)) == 0

    def test_first_bar_at_0931(self):
        assert data_guards.expected_bars_today(datetime(2026, 11, 4, 9, 31)) == 1

    def test_morning_cap(self):
        assert data_guards.expected_bars_today(datetime(2026, 11, 4, 11, 30)) == 120

    def test_lunch_freeze(self):
        assert data_guards.expected_bars_today(datetime(2026, 11, 4, 12, 30)) == 120

    def test_afternoon_resume(self):
        assert data_guards.expected_bars_today(datetime(2026, 11, 4, 13, 1)) == 121

    def test_close_cap(self):
        assert data_guards.expected_bars_today(datetime(2026, 11, 4, 15, 30)) == 240


class TestDropFormingBar:
    def test_drops_forming_last_bar(self):
        records = [bar(x) for x in minute_labels(DAY, 30)]        # 09:30..09:59
        now = datetime(2026, 11, 4, 10, 0, 12)                    # 09:59 完结于 10:00
        out, dropped = data_guards.drop_forming_bar(records + [bar("2026-11-04 10:00:00")],
                                                    1, now)
        assert dropped is True
        assert out[-1]["date"] == "2026-11-04 09:59:00"

    def test_keeps_completed_last_bar(self):
        records = [bar(x) for x in minute_labels(DAY, 30)]
        now = datetime(2026, 11, 4, 10, 0, 30)                    # 09:59 完结于 10:00，已过
        out, dropped = data_guards.drop_forming_bar(records, 1, now)
        assert dropped is False
        assert len(out) == 30

    def test_unparsable_label_kept(self):
        records = [bar("garbage")]
        out, dropped = data_guards.drop_forming_bar(records, 1, datetime.now())
        assert dropped is False and len(out) == 1


class TestGuardMinuteRecords:
    def test_healthy_passthrough_and_drop(self):
        labels = minute_labels(DAY, 31)                            # 09:30..10:00（最后一根在走）
        records = [bar(x) for x in labels]
        now = datetime(2026, 11, 4, 10, 0, 40)
        out, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is None
        assert out[-1]["date"] == "2026-11-04 09:59:00"           # 半根柱被丢掉

    def test_stale_when_source_stuck(self):
        records = [bar(x) for x in minute_labels(DAY, 30)]        # 最新完结 09:59
        now = datetime(2026, 11, 4, 10, 12)                       # 落后 12 分钟
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is not None and guard["skip_reason"] == "stale_data"
        assert "12" in guard["detail"] or "落后" in guard["detail"]

    def test_gap_when_today_short(self):
        # 中段断档：09:30..09:39 + 09:50..09:59（少了 10 根），最新柱新鲜
        # —— 年龄护栏不拦（它只管尾巴），连续性护栏负责抓住中段的洞
        labels = (minute_labels(DAY, 10)
                  + minute_labels(datetime(2026, 11, 4, 9, 50), 10))
        records = [bar(x) for x in labels]
        now = datetime(2026, 11, 4, 10, 0, 30)                    # 应有约 30 根，实际 20
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is not None and guard["skip_reason"] == "data_gap"
        assert guard["expected"] == 30 and guard["today_bars"] == 20

    def test_gap_tolerance_allows_few_missing(self):
        records = [bar(x) for x in minute_labels(DAY, 30)]
        # 少 3 根（在容忍 8 以内）：行情源偶尔丢几根是常态，不该拦
        records = records[:27]
        now = datetime(2026, 11, 4, 10, 0, 30)
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is None

    def test_non_monotonic_rejected(self):
        labels = minute_labels(DAY, 5)
        records = [bar(x) for x in labels]
        records[3], records[4] = records[4], records[3]            # 乱序
        now = datetime(2026, 11, 4, 9, 36)
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is not None and guard["skip_reason"] == "data_gap"
        assert "递增" in guard["detail"]

    def test_pre_open_auction_bar_blocked(self):
        # 2026-10-12 事故的护栏：09:27 时序列里出现当日 09:25 竞价柱 → 不可结算
        records = [bar("2026-11-03 14:59:00"), bar("2026-11-04 09:25:00")]
        now = datetime(2026, 11, 4, 9, 27, 4)
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is not None and guard["skip_reason"] == "data_gap"
        assert "盘前" in guard["detail"]

    def test_forming_only_series_is_stale(self):
        records = [bar("2026-11-04 10:00:00")]                    # 唯一一根还在走
        now = datetime(2026, 11, 4, 10, 0, 20)
        out, guard = data_guards.guard_minute_records(records, 1, now)
        assert out == [] and guard["skip_reason"] == "stale_data"

    def test_lunch_break_is_stale(self):
        # 午休期（11:30 后）无新柱：年龄护栏拦截（一次、留痕、不误结算）——
        # 13:01 后第一批午后柱完结即自动恢复
        records = [bar(x) for x in minute_labels(datetime(2026, 11, 4, 9, 30), 120)]
        now = datetime(2026, 11, 4, 12, 0)
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is not None and guard["skip_reason"] == "stale_data"

    def test_afternoon_resumes_normally(self):
        labels = (minute_labels(datetime(2026, 11, 4, 9, 30), 120)
                  + minute_labels(datetime(2026, 11, 4, 13, 0), 30))
        records = [bar(x) for x in labels]
        now = datetime(2026, 11, 4, 13, 30, 40)                   # 13:29 柱完结于 13:30
        out, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is None
        assert out[-1]["date"] == "2026-11-04 13:29:00"

    def test_bad_label_rejected(self):
        records = [bar(x) for x in minute_labels(DAY, 10)] + [bar("n/a")]
        now = datetime(2026, 11, 4, 9, 41)
        _, guard = data_guards.guard_minute_records(records, 1, now)
        assert guard is not None and guard["skip_reason"] == "data_gap"


class TestContractIntegration:
    def test_skip_reasons_in_contract(self):
        from agent import execution_contract as ec
        assert ec.SKIP_STALE_DATA in ec.SKIP_REASONS
        assert ec.SKIP_DATA_GAP in ec.SKIP_REASONS
        assert ec.normalize_skip_reason("stale_data") == "stale_data"
        assert ec.normalize_skip_reason("data_gap") == "data_gap"

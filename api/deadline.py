# -*- coding: utf-8 -*-
"""
全局时间预算(供 main 与 api.base 各层共用)

背景:GitHub Actions 单个 job 最多 6 小时,超时会被强杀(强杀后 job 输出为空,接力链会断)。
做法:main.py 启动时设置本轮预算;base.py 在执行长操作(视频播放/作业答题)过程中也会检查,
      达到上限立即抛出 TimeBudgetExceeded 优雅收尾,由工作流接力下一轮继续。

注意:TimeBudgetExceeded 继承 BaseException 而非 Exception,
      这样它不会被各层 `except Exception` 的兜底逻辑(跳过任务点)吞掉。
"""
import time

# 硬截止时间戳(0 表示未启用预算)
_deadline = 0.0

# 预留余量(秒):进入"软预算"区间后不再开始新的长任务(如长视频)
DEFAULT_RESERVE = 60


class TimeBudgetExceeded(BaseException):
    """本轮时间预算耗尽,交由工作流接力下一轮继续"""
    pass


def set_budget(minutes) -> None:
    """设置本轮时间预算(分钟);0 或空表示不限制"""
    global _deadline
    try:
        m = float(minutes or 0)
    except (TypeError, ValueError):
        m = 0
    _deadline = (time.time() + m * 60) if m > 0 else 0.0


def enabled() -> bool:
    """是否启用了时间预算"""
    return _deadline > 0


def remaining():
    """剩余秒数;未启用预算时返回 None"""
    return (_deadline - time.time()) if _deadline > 0 else None


def check() -> None:
    """硬预算:已达上限立即抛异常,让程序优雅收尾(避免被 6 小时上限强杀)"""
    if _deadline > 0 and time.time() >= _deadline:
        raise TimeBudgetExceeded()


def can_start(seconds_needed: float = 0, reserve: int = DEFAULT_RESERVE) -> bool:
    """软预算:判断剩余时间是否足够开始一个预计耗时 seconds_needed 秒的新任务。
    未启用预算时恒为 True。"""
    if _deadline <= 0:
        return True
    return (_deadline - time.time()) > (float(seconds_needed or 0) + reserve)

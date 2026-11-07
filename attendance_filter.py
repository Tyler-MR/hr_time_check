"""
考勤数据筛选逻辑
排除规则（两条）：
  1. 客服部普通员工排除（保留客服主管）
  2. 按姓名排除指定人员（郭松生、谢辉）

筛选规则：
  - 只统计下班卡 (checkType == "OffDuty")
  - 排除 18:30 之前打下班卡的
  - 纳入 18:30 ~ 18:45 之间打下班卡的员工
支持单日查询与日期区间（周期）查询。
"""

from datetime import datetime, timedelta
from typing import Optional

import config


def get_today_query_range() -> tuple[str, str]:
    """返回今天的查询时间范围：00:00:00 ~ 23:59:59。"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today.strftime("%Y-%m-%d %H:%M:%S")
    end = (today + timedelta(days=1) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    return start, end


def get_query_range_for_date(date_str: str) -> tuple[str, str]:
    """返回指定日期的查询时间范围。date_str 格式: "yyyy-MM-dd"。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start = dt.strftime("%Y-%m-%d %H:%M:%S")
    end = (dt + timedelta(days=1) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    return start, end


def get_query_range_for_period(start_date: str, end_date: str) -> tuple[str, str]:
    """
    返回日期区间的查询时间范围。
    start_date / end_date 格式: "yyyy-MM-dd"
    返回: (起始日 00:00:00, 结束日 23:59:59)
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt
    start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end = (end_dt + timedelta(days=1) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    return start, end


def split_date_range(start_date: str, end_date: str, max_days: int = 7) -> list[tuple[str, str]]:
    """
    将日期区间拆分成多个不超过 max_days 的子区间。
    原因: /attendance/list 接口要求 workDateFrom 与 workDateTo 相隔最多 7 天。

    返回: [(start_date, end_date), ...] 列表，日期格式 "yyyy-MM-dd"
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    segments: list[tuple[str, str]] = []
    current = start_dt
    while current <= end_dt:
        # 每段最多 max_days 天（含首尾），接口限制为相隔 7 天
        seg_end = min(current + timedelta(days=max_days - 1), end_dt)
        segments.append((current.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
        current = seg_end + timedelta(days=1)

    return segments


def parse_check_time(user_check_time_ms: int) -> Optional[datetime]:
    """将毫秒时间戳转为 datetime。"""
    if not user_check_time_ms:
        return None
    try:
        return datetime.fromtimestamp(user_check_time_ms / 1000)
    except (ValueError, OSError):
        return None


def build_time_thresholds(check_date: datetime = None) -> tuple[datetime, datetime]:
    """
    构建时间阈值。
    返回 (early_exclusion_threshold, off_duty_threshold):
      - early_exclusion_threshold: 18:30（此时间之前的下班卡排除）
      - off_duty_threshold: 18:45（此时间之前的下班卡纳入"早退"统计）

    参数:
        check_date: 基准日期（通常是打卡记录当天），为 None 则用今天
    """
    base = check_date or datetime.now()
    early_exclusion = base.replace(
        hour=config.OFF_DUTY_EARLY_EXCLUSION_HOUR,
        minute=config.OFF_DUTY_EARLY_EXCLUSION_MINUTE,
        second=0, microsecond=0,
    )
    off_duty = base.replace(
        hour=config.OFF_DUTY_THRESHOLD_HOUR,
        minute=config.OFF_DUTY_THRESHOLD_MINUTE,
        second=0, microsecond=0,
    )
    return early_exclusion, off_duty


def is_early_off_duty(record: dict) -> bool:
    """
    判断一条打卡记录是否符合"18:30 ~ 18:45 之间打了下班卡"。

    条件:
        1. checkType == "OffDuty"（下班打卡）
        2. timeResult != "NotSigned"（实际有打卡）
        3. checkTime >= 18:30（排除 18:30 前的下班卡）
        4. checkTime < 18:45（18:45 前打下班卡才算"早退"）

    阈值基于该条记录自身的打卡日期计算，因此多天查询时每一天独立判定。
    """
    if record.get("checkType") != "OffDuty":
        return False

    if record.get("timeResult") == "NotSigned":
        return False

    check_time = parse_check_time(record.get("userCheckTime"))
    if check_time is None:
        return False

    # 以打卡时间当天为基准构建阈值，保证跨天查询时每天独立判定
    early_exclusion_threshold, off_duty_threshold = build_time_thresholds(check_time)

    # 排除 18:30 之前的下班卡
    if check_time < early_exclusion_threshold:
        return False

    # 只纳入 18:45 之前的下班卡
    if check_time >= off_duty_threshold:
        return False

    return True


def filter_early_off_duty_employees(
    records: list[dict],
    excluded_user_ids: set[str],
) -> list[dict]:
    """
    从打卡记录中筛选符合条件的员工。

    参数:
        records: 所有打卡记录
        excluded_user_ids: 需要排除的 userId 集合
            （客服部普通员工 + 指定人员）

    返回按 (日期, 打卡时间) 排序的列表。
    """
    early_employees: dict[tuple[str, str], dict] = {}

    for record in records:
        if not is_early_off_duty(record):
            continue

        user_id = record.get("userId", "")

        # 排除客服部普通员工 / 指定人员
        if user_id in excluded_user_ids:
            continue

        check_time = parse_check_time(record.get("userCheckTime"))
        if check_time is None:
            continue

        work_date = record.get("workDate")
        if work_date:
            try:
                date_str = datetime.fromtimestamp(work_date / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError, TypeError):
                date_str = check_time.strftime("%Y-%m-%d")
        else:
            date_str = check_time.strftime("%Y-%m-%d")

        # 同一员工同一天多次打卡取最早
        key = (date_str, user_id)
        if key in early_employees:
            existing_time = parse_check_time(early_employees[key]["checkTimestamp"])
            if existing_time and check_time >= existing_time:
                continue

        early_employees[key] = {
            "userId": user_id,
            "date": date_str,
            "checkTime": check_time.strftime("%Y-%m-%d %H:%M:%S"),
            "checkTimestamp": record.get("userCheckTime"),
            "timeResult": record.get("timeResult", ""),
            "sourceType": record.get("sourceType", ""),
            "locationResult": record.get("locationResult", ""),
        }

    result = list(early_employees.values())
    # 按日期 + 打卡时间排序
    result.sort(key=lambda x: (x.get("date", ""), x.get("checkTimestamp", 0)))
    return result


def build_excluded_user_ids(
    client,
    all_user_ids: list[str],
) -> tuple[set[str], dict[str, str]]:
    """
    构建需要排除的 userId 集合。

    排除来源（两条规则）:
        1. 客服部普通员工（保留客服主管）
        2. 按姓名排除的特定人员（郭松生、谢辉）

    参数:
        client: DingTalkClient 实例
        all_user_ids: 企业所有用户 userId 列表

    返回:
        (excluded_user_ids, exclusion_reason_map)
        exclusion_reason_map: userId -> 排除原因
    """
    excluded: set[str] = set()
    exclusion_reasons: dict[str, str] = {}

    # --- 1. 排除客服部普通员工（保留客服主管） ---
    print("[Filter] 正在筛选客服部排除人员 ...")
    cs_excluded = client.get_customer_service_excluded_user_ids()

    for uid in cs_excluded:
        name = client.get_user_name(uid)
        excluded.add(uid)
        exclusion_reasons[uid] = f"客服部普通员工 ({name})"

    print(f"[Filter] 客服部普通员工排除: {len(cs_excluded)} 人")

    # --- 2. 按姓名排除特定人员 ---
    print(f"[Filter] 按姓名排除: {config.EXCLUDED_PERSONS_BY_NAME}")
    name_excluded = client.find_user_ids_by_names(config.EXCLUDED_PERSONS_BY_NAME)

    for uid in name_excluded:
        name = client.get_user_name(uid)
        excluded.add(uid)
        exclusion_reasons[uid] = f"指定排除 ({name})"

    print(f"[Filter] 按姓名排除: {len(name_excluded)} 人")

    # --- 汇总 ---
    total_excluded = len(excluded)
    effective_users = set(all_user_ids) - excluded
    print(f"[Filter] 排除总人数: {total_excluded}, 有效考勤人数: {len(effective_users)}")

    return excluded, exclusion_reasons

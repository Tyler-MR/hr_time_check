"""
结果输出模块
支持：控制台打印、CSV 文件、JSON 文件
功能：展示筛选结果 + 排除人员明细，支持单日与日期区间查询
"""

import csv
import json
import os
from collections import Counter
from datetime import datetime

import config


def build_aggregate_summary(early_employees: list[dict]) -> dict:
    """生成按人员、部门、职位统计的聚合数据。"""
    person_map: dict[str, dict] = {}
    department_counter: Counter[str] = Counter()
    position_counter: Counter[str] = Counter()

    for employee in early_employees:
        user_id = employee.get("userId", "")
        department = employee.get("department") or "未填写"
        position = employee.get("position") or "未填写"
        person = person_map.setdefault(
            user_id,
            {
                "userId": user_id,
                "userName": employee.get("userName", user_id),
                "department": department,
                "position": position,
                "count": 0,
            },
        )
        person["count"] += 1
        department_counter[department] += 1
        position_counter[position] += 1

    by_person = sorted(
        person_map.values(),
        key=lambda item: (-item["count"], item["userName"]),
    )
    by_department = [
        {"name": name, "count": count}
        for name, count in sorted(
            department_counter.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    by_position = [
        {"name": name, "count": count}
        for name, count in sorted(
            position_counter.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "byPerson": by_person,
        "byDepartment": by_department,
        "byPosition": by_position,
    }


def export_results(
    early_employees: list[dict],
    user_name_map: dict[str, str],
    exclusion_reasons: dict[str, str] = None,
    user_metadata_map: dict[str, dict[str, str]] = None,
    query_label: str = None,
    start_date: str = None,
    end_date: str = None,
):
    """
    将筛选结果输出到控制台 + CSV + JSON 文件。

    参数:
        early_employees:  筛选出的早退员工列表
        user_name_map:    userId -> 员工姓名 的映射
        exclusion_reasons: userId -> 排除原因（可选，用于报告排除明细）
        query_label:      查询范围描述（如 "2026-11-05" 或 "2026-11-01 ~ 2026-11-05"）
        start_date:       区间起始日 "yyyy-MM-dd"
        end_date:         区间结束日 "yyyy-MM-dd"
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    if exclusion_reasons is None:
        exclusion_reasons = {}
    if user_metadata_map is None:
        user_metadata_map = {}

    # 归一化查询范围
    if query_label is None:
        query_label = start_date or end_date or today_str
    if start_date is None:
        start_date = end_date = query_label

    is_period = start_date != end_date

    # 为每条记录添加姓名
    for emp in early_employees:
        emp["userName"] = user_name_map.get(emp["userId"], emp["userId"])
        metadata = user_metadata_map.get(emp["userId"], {})
        emp["department"] = metadata.get("department", emp.get("department", "未填写"))
        emp["position"] = metadata.get("position", emp.get("position", "未填写"))

    # 排序：按日期 + 打卡时间
    early_employees.sort(key=lambda x: (x.get("date", ""), x.get("checkTimestamp", 0)))

    # 构建排除人员列表
    excluded_list = []
    for uid, reason in exclusion_reasons.items():
        name = user_name_map.get(uid, uid)
        excluded_list.append({"userId": uid, "userName": name, "reason": reason})
    excluded_list.sort(key=lambda x: x["userName"])

    # 时间范围描述
    time_range = f"{config.OFF_DUTY_EARLY_EXCLUSION_HOUR}:{config.OFF_DUTY_EARLY_EXCLUSION_MINUTE:02d} ~ {config.OFF_DUTY_THRESHOLD_HOUR}:{config.OFF_DUTY_THRESHOLD_MINUTE:02d}"

    # ---- 控制台输出 ----
    print("\n" + "=" * 90)
    print(f"  考勤筛选结果 - 查询范围: {query_label}")
    print(f"  筛选条件：{time_range} 之间打了下班卡")
    print(f"  符合条件：{len(early_employees)} 条记录")
    print(f"  排除人员：{len(excluded_list)} 人")
    print("=" * 90)

    if early_employees:
        if is_period:
            print(f"\n{'序号':<5} {'日期':<12} {'姓名':<12} {'userId':<22} {'下班打卡时间':<22} {'打卡结果':<10} {'打卡来源':<10}")
            print("-" * 100)
        else:
            print(f"\n{'序号':<5} {'姓名':<12} {'userId':<22} {'下班打卡时间':<22} {'打卡结果':<10} {'打卡来源':<10} {'位置结果':<10}")
            print("-" * 95)

        for idx, emp in enumerate(early_employees, 1):
            if is_period:
                print(f"{idx:<5} {emp.get('date', ''):<12} {emp['userName']:<12} {emp['userId']:<22} "
                      f"{emp['checkTime']:<22} {emp['timeResult']:<10} {emp['sourceType']:<10}")
            else:
                print(f"{idx:<5} {emp['userName']:<12} {emp['userId']:<22} {emp['checkTime']:<22} "
                      f"{emp['timeResult']:<10} {emp['sourceType']:<10} {emp['locationResult']:<10}")
    else:
        print(f"\n  该范围内没有员工在 {time_range} 之间打下班卡。")

    if excluded_list:
        print(f"\n--- 排除人员明细 ({len(excluded_list)} 人) ---")
        print(f"{'姓名':<12} {'userId':<22} {'排除原因'}")
        print("-" * 60)
        for emp in excluded_list:
            print(f"{emp['userName']:<12} {emp['userId']:<22} {emp['reason']}")

    print("\n" + "=" * 90)

    # ---- CSV 输出 ----
    file_suffix = f"{start_date}_{end_date}" if is_period else start_date
    csv_path = os.path.join(config.OUTPUT_DIR, f"early_off_duty_{file_suffix}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        header = [
            "序号", "姓名", "userId", "部门", "职位", "下班打卡时间",
            "打卡结果", "打卡来源", "位置结果",
        ]
        if is_period:
            header.insert(1, "日期")
        writer.writerow(header)
        for idx, emp in enumerate(early_employees, 1):
            row = [idx]
            if is_period:
                row.append(emp.get("date", ""))
            row.extend([
                emp["userName"],
                emp["userId"],
                emp.get("department", ""),
                emp.get("position", ""),
                emp["checkTime"],
                emp["timeResult"],
                emp["sourceType"],
                emp["locationResult"],
            ])
            writer.writerow(row)
        # 排除明细
        writer.writerow([])
        writer.writerow([f"--- 排除人员明细 ({len(excluded_list)} 人) ---"])
        writer.writerow(["姓名", "userId", "排除原因"])
        for emp in excluded_list:
            writer.writerow([emp["userName"], emp["userId"], emp["reason"]])
    print(f"[输出] CSV 文件: {csv_path}")

    # ---- JSON 输出 ----
    json_path = os.path.join(config.OUTPUT_DIR, f"early_off_duty_{timestamp_str}.json")
    output_data = {
        "queryLabel": query_label,
        "queryDate": start_date if not is_period else f"{start_date} ~ {end_date}",
        "startDate": start_date,
        "endDate": end_date,
        "isPeriod": is_period,
        "threshold": time_range,
        "totalEarlyOffDuty": len(early_employees),
        "totalExcluded": len(excluded_list),
        "employees": early_employees,
        "excluded": excluded_list,
        "aggregates": build_aggregate_summary(early_employees),
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"[输出] JSON 文件: {json_path}")

    return output_data


def load_latest_report() -> dict | None:
    """加载最近一次输出的 JSON 报告。"""
    output_dir = config.OUTPUT_DIR
    if not os.path.exists(output_dir):
        return None

    json_files = [
        f for f in os.listdir(output_dir)
        if f.startswith("early_off_duty_") and f.endswith(".json")
    ]
    if not json_files:
        return None

    json_files.sort(reverse=True)
    latest_path = os.path.join(output_dir, json_files[0])

    with open(latest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_report_for_period(start_date: str, end_date: str) -> dict | None:
    """
    加载指定日期区间的报告（优先匹配 CSV 文件名的区间标记）。
    """
    output_dir = config.OUTPUT_DIR
    if not os.path.exists(output_dir):
        return None

    json_files = [
        f for f in os.listdir(output_dir)
        if f.startswith("early_off_duty_") and f.endswith(".json")
    ]
    if not json_files:
        return None

    # 优先找 queryDate 匹配的
    json_files.sort(reverse=True)
    for fname in json_files:
        path = os.path.join(output_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("startDate") == start_date and data.get("endDate") == end_date:
                return data
        except (json.JSONDecodeError, OSError):
            continue

    return None

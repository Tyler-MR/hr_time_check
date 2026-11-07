"""
钉钉考勤拉取 - 主程序

定时任务：每晚 20:30 自动执行
功能：拉取指定日期（支持单日或日期区间）的考勤数据，
      筛选 18:30~18:45 之间打下班卡的员工
排除规则：
  - 18:30 前打下班卡的排除
  - 客服部普通员工排除（保留客服主管）
  - 指定人员排除（郭松生、谢辉）

使用方式：
    python main.py                 # 立即执行一次（今天）
    python main.py --date 2026-11-05              # 指定单日
    python main.py --start 2026-11-01 --end 2026-11-05   # 指定区间
    python main.py --schedule      # 启动定时任务（每晚 20:30）
    python main.py --serve         # 启动 Web 服务（人事手机二次执行）
    python main.py --all           # 同时启动定时任务 + Web 服务
"""

import argparse
import sys
import threading
import time
from datetime import datetime

import config
from dingtalk_client import DingTalkClient
from attendance_filter import (
    get_today_query_range,
    get_query_range_for_date,
    get_query_range_for_period,
    split_date_range,
    filter_early_off_duty_employees,
    build_excluded_user_ids,
)
from reporter import export_results


def run_attendance_check(
    target_date: str = None,
    start_date: str = None,
    end_date: str = None,
) -> dict:
    """
    主流程：
        1. 获取所有员工 userId
        2. 构建排除集合（客服部普通员工 + 指定人员）
        3. 拉取打卡记录（仅有效用户，按 7 天分段查询）
        4. 筛选 18:30~18:45 之间打下班卡的员工
        5. 查询员工姓名
        6. 输出报告（控制台 + CSV + JSON）

    参数:
        target_date: 指定单日 "yyyy-MM-dd"，为 None 则取今天
        start_date:  区间起始日 "yyyy-MM-dd"
        end_date:    区间结束日 "yyyy-MM-dd"

    返回:
        报告数据 dict
    """
    print(f"\n{'=' * 80}")
    print(f"  钉钉考勤拉取 - 开始执行 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 80}")

    if not config.DINGTALK_APP_KEY or not config.DINGTALK_APP_SECRET:
        print("[错误] 请先配置 DINGTALK_APP_KEY 和 DINGTALK_APP_SECRET！")
        return {}

    # 归一化查询范围参数
    if start_date and end_date:
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        query_label = f"{start_date} ~ {end_date}"
        is_period = start_date != end_date
    elif target_date:
        start_date = end_date = target_date
        query_label = target_date
        is_period = False
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        start_date = end_date = today
        query_label = f"{today} (今天)"
        is_period = False

    try:
        client = DingTalkClient()

        # Step 1: 获取所有员工 userId
        print("\n[Step 1] 获取员工列表 ...")
        all_user_ids = client.get_all_user_ids()
        if not all_user_ids:
            print("[警告] 未获取到任何员工，流程结束。")
            return {}

        # Step 2: 构建排除集合
        print("\n[Step 2] 构建排除集合 ...")
        excluded_user_ids, exclusion_reasons = build_excluded_user_ids(client, all_user_ids)

        # 有效用户 = 全部 - 排除
        effective_user_ids = [uid for uid in all_user_ids if uid not in excluded_user_ids]
        print(f"[Step 2] 有效考勤人数: {len(effective_user_ids)}")

        if not effective_user_ids:
            print("[警告] 排除后没有有效员工，流程结束。")
            return {}

        # Step 3: 拉取打卡记录（按 7 天分段）
        print(f"\n[Step 3] 查询日期: {query_label}")
        all_records: list[dict] = []

        if is_period:
            segments = split_date_range(start_date, end_date, max_days=7)
            print(f"[Step 3] 区间共 {len(segments)} 段（受接口 7 天限制自动分段）")
            for idx, (seg_start, seg_end) in enumerate(segments, 1):
                seg_from, seg_to = get_query_range_for_period(seg_start, seg_end)
                print(f"\n[Step 3.{idx}] 分段 {seg_start} ~ {seg_end}")
                seg_records = client.get_attendance_records(
                    effective_user_ids, seg_from, seg_to
                )
                all_records.extend(seg_records)
        else:
            work_date_from, work_date_to = get_query_range_for_date(start_date)
            print(f"[Step 3] 查询时间范围: {work_date_from} ~ {work_date_to}")
            all_records = client.get_attendance_records(
                effective_user_ids, work_date_from, work_date_to
            )

        if not all_records:
            print("[警告] 未获取到任何打卡记录，流程结束。")
            return {}

        # Step 4: 筛选 18:30~18:45 之间打下班卡的员工
        threshold_str = f"{config.OFF_DUTY_EARLY_EXCLUSION_HOUR}:{config.OFF_DUTY_EARLY_EXCLUSION_MINUTE:02d} ~ {config.OFF_DUTY_THRESHOLD_HOUR}:{config.OFF_DUTY_THRESHOLD_MINUTE:02d}"
        print(f"\n[Step 4] 筛选 {threshold_str} 之间打下班卡的员工 ...")
        early_employees = filter_early_off_duty_employees(all_records, excluded_user_ids)

        # Step 5: 查询员工姓名
        print(f"\n[Step 5] 查询员工姓名 ({len(early_employees)} 条记录) ...")
        user_name_map: dict[str, str] = {}
        user_metadata_map: dict[str, dict[str, str]] = {}
        for emp in early_employees:
            uid = emp["userId"]
            if uid not in user_metadata_map:
                metadata = client.get_user_metadata(uid)
                user_metadata_map[uid] = metadata
                user_name_map[uid] = metadata["userName"]
                time.sleep(0.1)

        # Step 6: 输出报告
        print(f"\n[Step 6] 生成报告 ...")
        export_results(
            early_employees,
            user_name_map,
            exclusion_reasons,
            user_metadata_map=user_metadata_map,
            query_label=query_label,
            start_date=start_date,
            end_date=end_date,
        )

        print(f"\n[完成] 执行时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 返回报告数据供 Web 服务使用，并通过独立机器人转发给指定员工。
        from reporter import load_latest_report
        report = load_latest_report()
        if report and config.FORWARD_REPORT_ENABLED:
            try:
                from message_sender import send_report_to_user

                send_result = send_report_to_user(report)
                print(
                    f"[消息转发] 已提交给 {config.FORWARD_RECIPIENT_USER_ID}，"
                    f"task_id={send_result.get('task_id', '')}"
                )
            except Exception as send_error:
                # 消息发送失败不影响本地报告生成，但必须在日志中明确提示。
                print(f"[消息转发警告] 报告已生成，但发送失败: {send_error}")
        return report

    except Exception as e:
        print(f"\n[错误] 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def run_schedule():
    """启动定时任务模式。"""
    import schedule

    print(f"[定时模式] 每晚 {config.SCHEDULE_TIME} 自动执行考勤拉取 ...")
    print("按 Ctrl+C 退出。\n")

    schedule.every().day.at(config.SCHEDULE_TIME).do(run_attendance_check)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n[退出] 定时任务已停止。")


def run_web_server():
    """启动 Web 服务。"""
    from web_service import start_web_server
    start_web_server()


def run_all():
    """同时启动定时任务 + Web 服务。"""
    import schedule
    from web_service import start_web_server

    print(f"[全功能模式] 定时任务({config.SCHEDULE_TIME}) + Web 服务(:{config.WEB_PORT})")
    print("按 Ctrl+C 退出。\n")

    # 定时任务在子线程运行
    def schedule_loop():
        schedule.every().day.at(config.SCHEDULE_TIME).do(run_attendance_check)
        while True:
            schedule.run_pending()
            time.sleep(30)

    sched_thread = threading.Thread(target=schedule_loop, daemon=True)
    sched_thread.start()

    # Web 服务在主线程运行
    start_web_server()


def main():
    parser = argparse.ArgumentParser(
        description="钉钉考勤拉取 - 筛选 18:30~18:45 打下班卡的员工"
    )
    parser.add_argument("--schedule", action="store_true",
                        help="启动定时任务模式，每晚 20:30 自动执行")
    parser.add_argument("--serve", action="store_true",
                        help="启动 Web 服务，人事可通过手机访问并触发执行")
    parser.add_argument("--all", action="store_true",
                        help="同时启动定时任务 + Web 服务")
    parser.add_argument("--date", type=str, default=None,
                        help="指定查询日期 yyyy-MM-dd（默认今天）")
    parser.add_argument("--start", type=str, default=None,
                        help="区间查询起始日期 yyyy-MM-dd")
    parser.add_argument("--end", type=str, default=None,
                        help="区间查询结束日期 yyyy-MM-dd")
    args = parser.parse_args()

    if args.all:
        run_all()
    elif args.schedule:
        run_schedule()
    elif args.serve:
        run_web_server()
    elif args.start and args.end:
        run_attendance_check(start_date=args.start, end_date=args.end)
    else:
        run_attendance_check(target_date=args.date)


if __name__ == "__main__":
    main()

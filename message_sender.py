"""
消息转发机器人。

该模块使用独立的钉钉企业内部应用凭证发送工作通知，
与考勤数据拉取使用的应用凭证完全分开。
"""

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


MESSAGE_SEND_URL = f"{config.BASE_URL}/topapi/message/corpconversation/asyncsend_v2"


def _build_session() -> requests.Session:
    """创建消息发送会话，绕过本机失效代理并对临时网络错误重试。"""
    retry = Retry(
        total=config.DINGTALK_REQUEST_RETRIES,
        connect=config.DINGTALK_REQUEST_RETRIES,
        read=config.DINGTALK_REQUEST_RETRIES,
        status=config.DINGTALK_REQUEST_RETRIES,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.trust_env = False
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _build_session()


def get_forward_bot_access_token() -> str:
    """使用消息转发机器人的 Client ID/Secret 获取 access_token。"""
    response = _SESSION.get(
        config.TOKEN_URL,
        params={
            "appkey": config.FORWARD_BOT_APP_KEY,
            "appsecret": config.FORWARD_BOT_APP_SECRET,
        },
        timeout=config.DINGTALK_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"消息机器人获取 access_token 失败: {data.get('errmsg')}")
    return data["access_token"]


def build_report_message(report: dict[str, Any]) -> str:
    """将考勤报告整理成适合钉钉单聊展示的文本。"""
    employees = report.get("employees") or []
    lines = [
        "【考勤早退结果】",
        f"查询日期：{report.get('queryLabel') or report.get('queryDate', '')}",
        f"筛选时间：{report.get('threshold', '15:00 ~ 18:45')}",
        f"符合条件：{report.get('totalEarlyOffDuty', len(employees))} 条",
        f"排除人员：{report.get('totalExcluded', 0)} 人",
        "",
    ]

    if not employees:
        lines.append("符合条件的员工：无")
    else:
        lines.append("符合条件的员工：")
        for index, employee in enumerate(employees, 1):
            name = employee.get("userName") or employee.get("userId", "")
            check_time = employee.get("checkTime", "")
            lines.append(f"{index}. {name}  {check_time}")

    lines.extend([
        "",
        f"生成时间：{report.get('generatedAt', '')}",
    ])
    return "\n".join(lines)


def send_report_to_user(
    report: dict[str, Any],
    recipient_user_id: str | None = None,
) -> dict[str, Any]:
    """
    将报告通过消息机器人发送给一个员工。

    参数:
        report: reporter.py 生成的 JSON 报告字典。
        recipient_user_id: 接收人 userId，为空时使用配置中的默认接收人。
    """
    recipient = recipient_user_id or config.FORWARD_RECIPIENT_USER_ID
    token = get_forward_bot_access_token()
    payload = {
        "agent_id": int(config.FORWARD_BOT_AGENT_ID),
        "userid_list": recipient,
        "to_all_user": False,
        "msg": {
            "msgtype": "text",
            "text": {"content": build_report_message(report)},
        },
    }
    response = _SESSION.post(
        MESSAGE_SEND_URL,
        params={"access_token": token},
        json=payload,
        timeout=config.DINGTALK_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"发送考勤结果失败: {data.get('errmsg')}")
    return data


"""
钉钉考勤数据拉取 - 配置文件
凭证只从环境变量读取，避免将密钥提交到代码仓库。
"""

import os

# ===== 钉钉应用凭证 =====
DINGTALK_CORP_ID = os.environ.get("DINGTALK_CORP_ID", "")
DINGTALK_APP_KEY = os.environ.get("DINGTALK_APP_KEY", "")
DINGTALK_APP_SECRET = os.environ.get("DINGTALK_APP_SECRET", "")
DINGTALK_APP_ID = os.environ.get("DINGTALK_APP_ID", "")
DINGTALK_AGENT_ID = os.environ.get("DINGTALK_AGENT_ID", "")
DINGTALK_API_TOKEN = os.environ.get("DINGTALK_API_TOKEN", "")

# ===== 消息转发机器人凭证（独立于考勤应用） =====
# 用于把生成的考勤结果发送给指定钉钉员工，不参与考勤数据拉取。
FORWARD_BOT_APP_ID = os.environ.get(
    "FORWARD_BOT_APP_ID", ""
)
FORWARD_BOT_APP_KEY = os.environ.get("FORWARD_BOT_APP_KEY", "")
FORWARD_BOT_APP_SECRET = os.environ.get(
    "FORWARD_BOT_APP_SECRET",
    "",
)
FORWARD_BOT_AGENT_ID = os.environ.get("FORWARD_BOT_AGENT_ID", "")
FORWARD_RECIPIENT_USER_ID = os.environ.get(
    "FORWARD_RECIPIENT_USER_ID", ""
)
FORWARD_REPORT_ENABLED = os.environ.get(
    "FORWARD_REPORT_ENABLED", "0"
).strip().lower() not in {"0", "false", "no", "off"}

# ===== 定时任务参数 =====
# 每晚 20:30 执行
SCHEDULE_TIME = "20:30"

# ===== 筛选规则参数 =====

# 下班卡时间下限：18:30 之前打下班卡的排除
OFF_DUTY_EARLY_EXCLUSION_HOUR = 18
OFF_DUTY_EARLY_EXCLUSION_MINUTE = 30

# 下班卡时间上限：18:45 之前打的下班卡纳入"早退"统计
OFF_DUTY_THRESHOLD_HOUR = 18
OFF_DUTY_THRESHOLD_MINUTE = 45

# 根部门 ID（钉钉根部门 ID 固定为 1）
ROOT_DEPARTMENT_ID = 1

# ===== 人员排除规则 =====

# 规则 1: 客服部普通员工排除（保留客服主管）
# 规则 2: 按姓名排除指定人员（即使加入了考勤组也不算）
EXCLUDED_PERSONS_BY_NAME = ["郭松生", "谢辉"]

# 客服部排除：名称包含以下关键词的部门视为客服部，排除其普通员工，保留主管
CUSTOMER_SERVICE_DEPT_KEYWORDS = ["客服"]

# ===== Web 服务参数（人事手机二次执行）=====
WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))
# 简单访问 PIN 码，人事访问网页时需要输入
WEB_ACCESS_PIN = os.environ.get("WEB_ACCESS_PIN", "8888")

# ===== API 端点 =====
BASE_URL = "https://oapi.dingtalk.com"

# 获取 access_token
TOKEN_URL = f"{BASE_URL}/gettoken"

# 获取子部门列表
DEPARTMENT_LIST_URL = f"{BASE_URL}/topapi/v2/department/listsub"

# 获取部门详情（含主管 userId 列表）
DEPARTMENT_DETAIL_URL = f"{BASE_URL}/topapi/v2/department/get"

# 获取部门用户 userid 列表
USER_LIST_ID_URL = f"{BASE_URL}/topapi/user/listid"

# 获取用户详情（用于拿到姓名）
USER_DETAIL_URL = f"{BASE_URL}/topapi/v2/user/get"

# 获取打卡结果
ATTENDANCE_LIST_URL = f"{BASE_URL}/attendance/list"

# ===== 分页与限流参数 =====
# /attendance/list 接口每次最多查询 50 个用户
ATTENDANCE_BATCH_SIZE = 50

# /attendance/list 的 limit 参数最大值
ATTENDANCE_LIMIT = 50

# 钉钉接口网络参数。接口偶发超时，客户端会自动重试只读请求。
DINGTALK_REQUEST_TIMEOUT = float(os.environ.get("DINGTALK_REQUEST_TIMEOUT", "20"))
DINGTALK_REQUEST_RETRIES = int(os.environ.get("DINGTALK_REQUEST_RETRIES", "2"))
DINGTALK_API_RETRY_WAIT_SECONDS = float(
    os.environ.get("DINGTALK_API_RETRY_WAIT_SECONDS", "2")
)

# access_token 缓存文件路径
TOKEN_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".token_cache.json")

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

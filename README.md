# 钉钉考勤拉取工具

开发、部署和故障排查请参阅 [DEVELOPMENT.md](DEVELOPMENT.md)。

每晚 20:30 自动拉取当天钉钉考勤数据，筛选出 **18:30 ~ 18:45 之间打了下班卡** 的员工，供人事审核。

支持 **按日期查询**（单日）和 **按周期查询**（日期区间），人事可通过手机网页选择日期或区间重新拉取数据。

## 筛选规则

### 纳入条件（同时满足）
1. `checkType == "OffDuty"` — 下班打卡
2. `timeResult != "NotSigned"` — 实际有打卡（非未打卡占位）
3. 打卡时间 >= 18:30 — 排除 18:30 前的下班卡
4. 打卡时间 < 18:45 — 18:45 前打下班卡才算"早退"

### 排除规则
| 规则 | 说明 | 实现方式 |
|------|------|---------|
| 客服部普通员工 | 客服部除了客服主管，其他人不参与计算 | 通过 `/topapi/v2/department/get` 获取 `dept_manager_userid_list`，主管保留，普通员工排除 |
| 按姓名排除 | 郭松生、谢辉 | 通过 `/topapi/v2/user/get` 获取姓名匹配 userId 后排除（即使加入了考勤组也不算） |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

凭证不写入代码。复制 `.env.example` 为项目目录下的 `.env` 并填写配置，程序启动时会自动读取；也可以在启动服务前设置环境变量，系统环境变量优先：

```bash
export DINGTALK_CORP_ID="your_corp_id"
export DINGTALK_APP_KEY="your_app_key"
export DINGTALK_APP_SECRET="your_app_secret"
export DINGTALK_AGENT_ID="your_agent_id"
export WEB_ACCESS_PIN="8888"  # 人事网页访问 PIN 码
```

### 消息转发机器人

消息转发使用独立的钉钉企业内部应用，不会替换上面的考勤应用凭证。设置 `FORWARD_REPORT_ENABLED=1` 后，每次成功生成报告会发送给配置的员工 userId：

```bash
export FORWARD_REPORT_ENABLED="1"
export FORWARD_BOT_APP_KEY="your_forward_bot_client_id"
export FORWARD_BOT_APP_SECRET="your_forward_bot_client_secret"
export FORWARD_BOT_AGENT_ID="your_forward_bot_agent_id"
export FORWARD_RECIPIENT_USER_ID="your_recipient_user_id"
```

如需暂时关闭自动转发，将 `FORWARD_REPORT_ENABLED` 设置为 `0`。

### 3. 钉钉应用权限

在钉钉开发者后台为应用添加以下权限：
- 企业考勤数据读权限
- 考勤组查询权限
- 成员信息读权限
- 通讯录部门信息读权限

## 运行方式

### 立即执行一次（默认查今天）

```bash
python main.py
```

### 指定单日查询

```bash
python main.py --date 2026-11-05
```

### 指定日期区间（周期）查询

```bash
python main.py --start 2026-11-01 --end 2026-11-05
```

> **注意：** 钉钉 `/attendance/list` 接口要求查询区间不超过 7 天。本工具会自动将超过 7 天的区间拆分为多个子区间分段查询，无需手动处理。

### 启动定时任务（每晚 20:30）

```bash
python main.py --schedule
```

### 启动 Web 服务（人事手机二次执行）

```bash
python main.py --serve
```

### 同时启动定时任务 + Web 服务（推荐生产部署）

```bash
python main.py --all
```

## CLI 参数一览

| 参数 | 说明 |
|------|------|
| `--date YYYY-MM-DD` | 查询指定单日 |
| `--start YYYY-MM-DD` | 区间查询起始日期（与 `--end` 配合使用） |
| `--end YYYY-MM-DD` | 区间查询结束日期（与 `--start` 配合使用） |
| `--schedule` | 启动定时任务模式 |
| `--serve` | 启动 Web 服务模式 |
| `--all` | 同时启动定时任务 + Web 服务 |

## 日期选择与周期查询

### 功能说明

人事可能需要统计某个周期或某天的考勤数据，本工具支持两种查询模式：

- **单日模式：** 选择一个日期，查询当天 18:30~18:45 之间打下班卡的员工
- **周期模式：** 选择起始日期和结束日期，查询整个区间的数据

### 7 天自动分段

钉钉 `/attendance/list` 接口限制 `workDateFrom` 与 `workDateTo` 相隔最多 7 天。当查询区间超过 7 天时，`split_date_range()` 会自动拆分：

```
2026-11-01 ~ 2026-11-30 (30天)
  → 分段1: 2026-11-01 ~ 2026-11-07
  → 分段2: 2026-11-08 ~ 2026-11-14
  → 分段3: 2026-11-15 ~ 2026-11-21
  → 分段4: 2026-11-22 ~ 2026-11-28
  → 分段5: 2026-11-29 ~ 2026-11-30
```

每段独立查询后合并结果，对使用者透明。

### 多天去重规则

同一员工同一天多次打卡取**最早**一次。不同日期的记录独立保留，即同一员工在多天有早退记录会分别显示。

## 人事手机二次执行方案

### 原理

启动 Flask Web 服务后，人事在手机浏览器访问 `http://服务器IP:5000`，输入 PIN 码（默认 8888），即可：

1. **选择查询日期** — 支持单日/周期两种模式切换，提供快捷按钮（今天、昨天、近7天、近30天）
2. **一键拉取考勤** — 点击按钮触发拉取指定日期范围的考勤数据（异步执行，页面实时显示执行状态）
3. **查看筛选结果** — 显示筛选出的员工列表、日期（周期模式）、打卡时间、打卡结果
4. **下载 Excel** — 下载当前报告，工作簿包含人员、部门、职位和明细四个工作表

### 手机界面

```
┌─────────────────────────┐
│  考勤筛选系统            │
│  筛选 18:30~18:45 打下班卡 │
├─────────────────────────┤
│  请输入访问 PIN 码        │
│  [ _ _ _ _ ]             │
│  [    进入    ]          │
└─────────────────────────┘

      ↓ PIN 验证通过 ↓

┌─────────────────────────┐
│  选择查询范围            │
│  [ 单日 ]  [ 周期 ]      │
├─────────────────────────┤
│  ← 单日模式 →            │
│  查询日期: [2026-11-05]  │
│                          │
│  [今天] [昨天] [上周同天] │
│  [近7天] [近30天]        │
│                          │
│  [    拉取并筛选    ]    │
├─────────────────────────┤
│  符合条件: 3   排除: 5   │
│  上次执行: 20:30:15      │
├─────────────────────────┤
│  筛选结果        3 条    │
│  ┌──┬────┬──────┬─────┐ │
│  │# │姓名│下班卡│结果 │ │
│  ├──┼────┼──────┼─────┤ │
│  │1 │张三│16:32 │Early│ │
│  │2 │李四│17:05 │Early│ │
│  │3 │王五│18:20 │Early│ │
│  └──┴────┴──────┴─────┘ │
│  [ 下载 CSV 报表 ]       │
├─────────────────────────┤
│  ← 周期模式 →            │
│  起始: [2026-11-01]      │
│  结束: [2026-11-05]      │
│  超过7天自动分段查询      │
│                          │
│  ┌──┬──────┬────┬──────┐│
│  │# │日期  │姓名│下班卡││
│  ├──┼──────┼────┼──────┤│
│  │1 │11-02 │张三│17:30 ││
│  │2 │11-03 │李四│18:10 ││
│  │3 │11-05 │王五│16:45 ││
│  └──┴──────┴────┴──────┘│
├─────────────────────────┤
│  排除说明                │
│  1. 18:30前下班卡排除     │
│  2. 客服部普通员工排除     │
│  3. 指定人员排除(郭/谢)   │
└─────────────────────────┘
```

### 快捷日期按钮

| 按钮 | 效果 |
|------|------|
| 今天 | 单日模式，日期设为今天 |
| 昨天 | 单日模式，日期设为昨天 |
| 上周同一天 | 单日模式，日期设为 7 天前 |
| 近7天 | 周期模式，起始=7天前，结束=今天 |
| 近30天 | 周期模式，起始=30天前，结束=今天 |

### API 接口（可选集成）

| 端点 | 方法 | 说明 | 认证 | 参数 |
|------|------|------|------|------|
| `/api/status` | GET | 获取执行状态 | 无 | — |
| `/api/trigger` | POST | 触发重新执行 | `X-API-Token` 头 | `?start=YYYY-MM-DD&end=YYYY-MM-DD`（可选，默认今天） |
| `/api/report` | GET | 获取报告 JSON | `X-API-Token` 头 | `?start=YYYY-MM-DD&end=YYYY-MM-DD`（可选，默认最新） |

API 调用示例：

```bash
# 触发查询 11月1日~5日 的考勤
curl -X POST -H "X-API-Token: 8888" \
  "http://localhost:5000/api/trigger?start=2026-11-01&end=2026-11-05"

# 获取指定区间的报告
curl -H "X-API-Token: 8888" \
  "http://localhost:5000/api/report?start=2026-11-01&end=2026-11-05"
```

## 文件结构

```
dingtalk-attendance/
├── config.py              # 配置（凭证、时间阈值、排除规则、API端点）
├── dingtalk_client.py     # 钉钉 API 客户端（token、部门、用户、考勤组、打卡）
├── attendance_filter.py   # 筛选逻辑（日期分段、18:30~18:45、客服部排除、姓名排除）
├── reporter.py            # 结果输出（控制台 + CSV + JSON + 排除明细 + 区间报告加载）
├── web_service.py         # Flask Web 服务（日期选择、周期查询、人事手机端）
├── main.py                # 主程序（--date / --start --end / --schedule / --serve / --all）
├── requirements.txt       # Python 依赖
├── output/                # 输出目录（自动创建）
│   ├── early_off_duty_2026-11-06.csv            # 单日 CSV
│   ├── early_off_duty_2026-11-01_2026-11-05.csv # 周期 CSV
│   └── early_off_duty_20261106_203015.json      # JSON 报告（含区间信息）
└── .token_cache.json      # access_token 本地缓存
```

## 使用的钉钉 API

| API | 用途 | 端点 |
|-----|------|------|
| 获取 access_token | 身份鉴权 | `GET /gettoken` |
| 获取子部门列表 | 遍历组织架构 | `POST /topapi/v2/department/listsub` |
| 获取部门详情 | 获取主管 userId 列表 | `POST /topapi/v2/department/get` |
| 获取部门用户ID | 获取员工 userId | `POST /topapi/user/listid` |
| 获取用户详情 | 查询员工姓名 | `POST /topapi/v2/user/get` |
| 获取打卡结果 | 拉取考勤数据 | `POST /attendance/list` |

> **接口兼容与网络容错：** `/topapi/user/listid` 当前常见返回字段为 `userid_list`，程序同时兼容旧格式 `user_id_list`；`/attendance/list` 的分页字段为 `hasMore`，程序兼容 `has_more`。部门树和部门用户列表会在一次执行中缓存，避免重复触发 QPS 限流；钉钉只读请求默认超时 20 秒，并自动重试 2 次。业务层 `90002` 限流也会退避重试，最终失败会终止本次查询，避免生成不完整报告。相关参数可通过 `DINGTALK_REQUEST_TIMEOUT`、`DINGTALK_REQUEST_RETRIES` 和 `DINGTALK_API_RETRY_WAIT_SECONDS` 调整。

## 输出报告格式

### 聚合查看

网页报告提供“聚合查看”按钮，默认只展示汇总数据；需要查看长明细时，点击聚合页签后的“明细数据”按钮：

- 按人员：显示人员、部门、职位、早退次数
- 按部门：统计各部门早退次数
- 按职位：统计各职位早退次数
- 明细数据：按日期、姓名、下班卡时间展示原始筛选结果

JSON 报告中的 `aggregates` 字段保存同样的三组数据。网页下载的 Excel 文件包含“按人员”“按部门”“按职位”“明细数据”四个工作表；明细包含部门和职位等字段。

### CSV 文件命名

- 单日：`early_off_duty_2026-11-06.csv`
- 周期：`early_off_duty_2026-11-01_2026-11-05.csv`

### JSON 报告字段

```json
{
  "queryLabel": "2026-11-01 ~ 2026-11-05",
  "queryDate": "2026-11-01 ~ 2026-11-05",
  "startDate": "2026-11-01",
  "endDate": "2026-11-05",
  "isPeriod": true,
  "threshold": "18:30 ~ 18:45",
  "totalEarlyOffDuty": 3,
  "totalExcluded": 5,
  "employees": [
    {
      "userId": "user001",
      "userName": "张三",
      "date": "2026-11-02",
      "checkTime": "2026-11-02 17:30:00",
      "timeResult": "Early",
      "sourceType": "USER",
      "locationResult": "Normal"
    }
  ],
  "excluded": [
    { "userId": "user004", "userName": "郭松生", "reason": "指定排除 (郭松生)" }
  ],
  "generatedAt": "2026-11-05 20:30:00"
}
```

## 生产部署建议

### Linux（推荐）

```bash
# 1. 使用 systemd 管理服务
sudo cat > /etc/systemd/system/dingtalk-attendance.service << 'EOF'
[Unit]
Description=DingTalk Attendance Service
After=network.target

[Service]
Type=simple
User=app
WorkingDirectory=/opt/dingtalk-attendance
ExecStart=/usr/bin/python3 main.py --all
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable dingtalk-attendance
sudo systemctl start dingtalk-attendance
```

### Windows

使用任务计划程序：
1. 创建基本任务，设置触发器为"计算机启动时"
2. 操作：启动程序 `python.exe`，参数 `main.py --all`
3. 起始目录设为项目路径

### 网络要求

- 服务器需能访问 `https://oapi.dingtalk.com`
- 如需手机外网访问 Web 服务，需配置端口映射或反向代理
- 建议通过 Nginx 反向代理 + HTTPS 提升安全性

### 安全建议

- 修改 `WEB_ACCESS_PIN` 为强密码
- 通过 Nginx 添加 IP 白名单限制
- 将 `config.py` 中的密钥改为环境变量注入
- 定期检查钉钉应用权限范围

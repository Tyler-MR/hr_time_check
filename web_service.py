"""
Flask Web 服务 - 人事手机端二次执行入口

功能:
  - 移动端友好的网页界面
  - 可选择查询日期（单日）或日期区间（周期统计）
  - 查看最近一次考勤筛选结果
  - 一键触发重新拉取考勤数据
  - 简单 PIN 码认证

部署:
  python main.py --serve
  人事在手机浏览器访问 http://服务器IP:5000
"""

import os
import json
import threading
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, redirect, render_template_string

import config

app = Flask(__name__)

# 全局状态
_run_lock = threading.Lock()
_last_run_status: dict = {
    "running": False,
    "lastRunTime": "",
    "lastRunResult": None,
    "message": "",
}


def _load_latest_report() -> dict | None:
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


def _load_latest_csv_path(report: dict = None) -> str | None:
    """获取与报告对应的 CSV 文件路径。"""
    output_dir = config.OUTPUT_DIR
    if not os.path.exists(output_dir):
        return None

    csv_files = [
        f for f in os.listdir(output_dir)
        if f.startswith("early_off_duty_") and f.endswith(".csv")
    ]
    if not csv_files:
        return None

    # 优先匹配报告的日期区间
    if report:
        start = report.get("startDate")
        end = report.get("endDate")
        if start and end:
            suffix = f"{start}_{end}" if start != end else start
            target = f"early_off_duty_{suffix}.csv"
            if target in csv_files:
                return os.path.join(output_dir, target)

    csv_files.sort(reverse=True)
    return os.path.join(output_dir, csv_files[0])


def _do_attendance_check_async(start_date: str = None, end_date: str = None):
    """在后台线程中执行考勤拉取。"""
    global _last_run_status

    if not _run_lock.acquire(blocking=False):
        return

    try:
        _last_run_status["running"] = True
        _last_run_status["message"] = "正在执行..."
        _last_run_status["lastRunTime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 延迟导入，避免循环依赖
        from main import run_attendance_check

        if start_date and end_date and start_date != end_date:
            result = run_attendance_check(start_date=start_date, end_date=end_date)
        elif start_date:
            result = run_attendance_check(target_date=start_date)
        else:
            result = run_attendance_check()

        _last_run_status["running"] = False
        _last_run_status["lastRunResult"] = result or _load_latest_report()
        if _last_run_status["lastRunResult"]:
            _last_run_status["message"] = "执行完成"
        else:
            # run_attendance_check 在没有数据或发生异常时可能返回空字典。
            # 不能继续显示“执行完成”，否则页面会掩盖真实状态。
            _last_run_status["message"] = "执行结束，但没有生成报告，请查看服务日志"
    except Exception as e:
        _last_run_status["running"] = False
        _last_run_status["message"] = f"执行失败: {e}"
    finally:
        _run_lock.release()


# ------------------------------------------------------------------ #
#  页面模板
# ------------------------------------------------------------------ #

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>考勤筛选 - 人事审核</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 760px; min-height: 100vh; margin: 0 auto;
    background: #edf3f8; color: #223044; padding: 14px;
  }
  .header {
    background: linear-gradient(135deg, #176dff, #14b4e8);
    color: #fff; padding: 22px 18px; border-radius: 18px;
    text-align: left; margin-bottom: 14px; box-shadow: 0 8px 22px rgba(23,109,255,.18);
  }
  .header h1 { font-size: 21px; font-weight: 800; }
  .header p { font-size: 12px; opacity: 0.88; margin-top: 7px; }
  .card {
    background: #fff; border: 1px solid #e1e9f2; border-radius: 16px; padding: 14px;
    margin-bottom: 12px; box-shadow: 0 5px 16px rgba(40,68,100,.07);
  }
  .card-title { font-size: 16px; font-weight: 800; margin-bottom: 12px; }
  .btn {
    display: block; width: 100%; padding: 14px; border: none;
    border-radius: 10px; font-size: 16px; font-weight: 600;
    text-align: center; cursor: pointer; transition: opacity 0.2s;
    text-decoration: none;
  }
  .btn:active { opacity: 0.7; }
  .btn-primary { background: #176dff; color: #fff; }
  .btn:disabled { background: #ccc; color: #999; cursor: not-allowed; }
  .status-badge {
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 12px; font-weight: 500;
  }
  .status-running { background: #fff3e0; color: #e65100; }
  .status-idle { background: #e8f5e9; color: #2e7d32; }
  .status-error { background: #ffebee; color: #c62828; }
  .table-scroll { overflow-x: auto; border-radius: 12px; }
  table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: 12px; }
  th { background: #eef4fa; color: #66778a; padding: 9px 8px; text-align: left; font-weight: 700; }
  td { padding: 9px 8px; border-bottom: 1px solid #edf0f4; white-space: nowrap; }
  .meta { font-size: 12px; color: #999; margin-top: 8px; }
  .empty { text-align: center; color: #999; padding: 24px; font-size: 14px; }
  .pin-input {
    width: 100%; padding: 12px; border: 2px solid #ddd;
    border-radius: 10px; font-size: 18px; text-align: center;
    letter-spacing: 8px; margin-bottom: 12px;
  }
  .pin-input:focus { border-color: #0089ff; }
  .count-badge {
    display: inline-block; background: #ff5722; color: #fff;
    padding: 2px 12px; border-radius: 20px; font-size: 14px;
    font-weight: 600; margin-left: 8px;
  }

  /* 日期选择区域 */
  .tabs { display: flex; gap: 8px; margin-bottom: 14px; }
  .tab {
    flex: 1; padding: 9px; text-align: center; border-radius: 8px;
    font-size: 13px; font-weight: 500; cursor: pointer;
    background: #f0f4f8; color: #666; border: 1px solid transparent;
  }
  .tab.active { background: #176dff; color: #fff; }
  .date-field { margin-bottom: 12px; }
  .date-field label {
    display: block; font-size: 12px; color: #888;
    margin-bottom: 6px; font-weight: 500;
  }
  .date-field input {
    width: 100%; padding: 11px; border: 1px solid #ddd;
    border-radius: 10px; font-size: 15px; background: #fff;
  }
  .date-field input:focus { border-color: #176dff; outline: none; }
  .quick-dates { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .quick-date {
    padding: 7px 12px; border-radius: 16px; background: #f0f4f8;
    font-size: 12px; color: #555; cursor: pointer; border: none;
  }
  .quick-date:active { background: #dbe7f3; }
  .hint { font-size: 11px; color: #aaa; margin-top: 6px; }
  .summary-box {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px;
  }
  .summary-item {
    flex: 1; background: #f7f9fb; border-radius: 8px;
    padding: 11px 5px; text-align: center;
  }
  .summary-item .num { font-size: 20px; font-weight: 800; color: #176dff; }
  .summary-item .lbl { font-size: 11px; color: #999; margin-top: 2px; }
  .aggregate-toolbar { margin-top: 14px; }
  .aggregate-btn {
    width: 100%; padding: 11px; border: 1px solid #0089ff;
    border-radius: 9px; background: #fff; color: #0089ff;
    font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .aggregate-panel { margin-top: 12px; padding: 12px; background: #f7f9fb; border-radius: 10px; }
  .aggregate-tabs {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px; margin-bottom: 10px;
  }
  .aggregate-tab {
    min-width: 0; padding: 8px 4px; border: none; border-radius: 7px;
    background: #e8eef5; color: #667; cursor: pointer; font-size: 12px;
  }
  .aggregate-tab.active { background: #0089ff; color: #fff; }
  .aggregate-table { width: 100%; border-collapse: collapse; font-size: 12px; background: #fff; }
  .aggregate-table th, .aggregate-table td { padding: 7px 6px; border-bottom: 1px solid #edf0f3; }
  .aggregate-table th { background: #eef4fa; }
  .aggregate-count { color: #0089ff; font-weight: 700; text-align: right; }
  .collapse-btn {
    float: right; padding: 5px 10px; border: 1px solid #d8e2ec;
    border-radius: 7px; background: #f7f9fb; color: #557;
    font-size: 12px; cursor: pointer;
  }
</style>
</head>
<body>

<div class="header">
  <h1>考勤筛选系统</h1>
  <p>筛选 18:30~18:45 打下班卡员工 | 每晚 20:30 自动执行</p>
</div>

{% if not authenticated %}
<div class="card">
  <div class="card-title">请输入访问 PIN 码</div>
  <form method="POST" action="/login">
    <input class="pin-input" type="password" name="pin" placeholder="PIN" maxlength="4" autofocus>
    <button class="btn btn-primary" type="submit">进入</button>
  </form>
</div>
{% else %}

<div class="card">
  <div class="card-title">选择查询范围</div>

  <div class="tabs">
    <div class="tab {% if not is_period %}active{% endif %}" onclick="switchMode('single')">单日</div>
    <div class="tab {% if is_period %}active{% endif %}" onclick="switchMode('period')">周期</div>
  </div>

  <form method="POST" action="/trigger" id="queryForm">
    <input type="hidden" name="mode" id="queryMode" value="{{ 'period' if is_period else 'single' }}">
    <!-- 单日模式 -->
    <div id="singleMode" style="display: {% if is_period %}none{% else %}block{% endif %}">
      <div class="date-field">
        <label>查询日期</label>
        <input type="date" name="date" id="dateInput" value="{{ selected_date }}" max="{{ today }}">
      </div>
    </div>

    <!-- 周期模式 -->
    <div id="periodMode" style="display: {% if is_period %}block{% else %}none{% endif %}">
      <div class="date-field">
        <label>起始日期</label>
        <input type="date" name="start" id="startInput" value="{{ selected_start }}" max="{{ today }}">
      </div>
      <div class="date-field">
        <label>结束日期</label>
        <input type="date" name="end" id="endInput" value="{{ selected_end }}" max="{{ today }}">
      </div>
      <p class="hint">超过 7 天的周期会自动分段查询</p>
    </div>

    <div class="quick-dates">
      <button type="button" class="quick-date" onclick="setDate(0)">今天</button>
      <button type="button" class="quick-date" onclick="setDate(1)">昨天</button>
      <button type="button" class="quick-date" onclick="setDate(7)">上周同一天</button>
      <button type="button" class="quick-date" onclick="setPeriod(6)">近7天</button>
      <button type="button" class="quick-date" onclick="setPeriod(29)">近30天</button>
    </div>

    <button class="btn btn-primary" type="submit" style="margin-top:14px"
      {% if status.running %}disabled{% endif %}>
      {% if status.running %}正在执行...{% else %}拉取并筛选{% endif %}
    </button>
  </form>

  <div class="summary-box">
    <div class="summary-item">
      <div class="num">{{ report.totalEarlyOffDuty if report else 0 }}</div>
      <div class="lbl">符合条件</div>
    </div>
    <div class="summary-item">
      <div class="num">{{ report.totalExcluded if report else 0 }}</div>
      <div class="lbl">排除人员</div>
    </div>
    <div class="summary-item">
      <div class="num">{{ report.aggregates.byPerson|length if report and report.aggregates else 0 }}</div>
      <div class="lbl">涉及人员</div>
    </div>
  </div>

  <p class="meta">
    上次执行: {{ status.lastRunTime or (report.generatedAt if report else "尚未执行") }}<br>
    {% if report %}当前报表范围: {{ report.queryLabel }}{% else %}{{ status.message }}{% endif %}
  </p>
</div>

{% if report %}
<div class="card">
  <div class="card-title">
    筛选结果
    <span class="count-badge">{{ report.totalEarlyOffDuty }} 条</span>
    <button class="collapse-btn" type="button" onclick="toggleResultPanel(this)">收起</button>
  </div>
  <div id="resultPanel">
  <p class="meta">查询范围: {{ report.queryLabel }} | 阈值: {{ report.threshold }}</p>
  <p class="meta">生成时间: {{ report.generatedAt }}</p>
  <p class="meta">明细数据已收起，请在下方“聚合查看”中点击“明细数据”查看。</p>
  </div>
</div>

{% if report.aggregates %}
<div class="aggregate-toolbar">
  <button class="aggregate-btn" type="button" onclick="toggleAggregatePanel()">聚合查看</button>
</div>
<div id="aggregatePanel" class="aggregate-panel" style="display:none">
  <div class="aggregate-tabs">
    <button class="aggregate-tab active" type="button" onclick="switchAggregate('person', this)">按人员</button>
    <button class="aggregate-tab" type="button" onclick="switchAggregate('department', this)">按部门</button>
    <button class="aggregate-tab" type="button" onclick="switchAggregate('position', this)">按职位</button>
    <button class="aggregate-tab" type="button" onclick="switchAggregate('detail', this)">明细数据</button>
  </div>

  <div id="aggregate-person">
    <table class="aggregate-table">
      <thead><tr><th>人员</th><th>部门</th><th>职位</th><th>次数</th></tr></thead>
      <tbody>
      {% for item in report.aggregates.byPerson %}
        <tr><td>{{ item.userName }}</td><td>{{ item.department }}</td><td>{{ item.position }}</td><td class="aggregate-count">{{ item.count }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div id="aggregate-department" style="display:none">
    <table class="aggregate-table">
      <thead><tr><th>部门</th><th>次数</th></tr></thead>
      <tbody>
      {% for item in report.aggregates.byDepartment %}
        <tr><td>{{ item.name }}</td><td class="aggregate-count">{{ item.count }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div id="aggregate-position" style="display:none">
    <table class="aggregate-table">
      <thead><tr><th>职位</th><th>次数</th></tr></thead>
      <tbody>
      {% for item in report.aggregates.byPosition %}
        <tr><td>{{ item.name }}</td><td class="aggregate-count">{{ item.count }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div id="aggregate-detail" style="display:none">
    {% if report.employees %}
    <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>#</th>
          {% if report.isPeriod %}<th>日期</th>{% endif %}
          <th>姓名</th>
          <th>下班卡</th>
          <th>结果</th>
        </tr>
      </thead>
      <tbody>
        {% for emp in report.employees %}
        <tr>
          <td>{{ loop.index }}</td>
          {% if report.isPeriod %}<td>{{ emp.date[5:] }}</td>{% endif %}
          <td>{{ emp.userName }}</td>
          <td>{{ emp.checkTime[11:] }}</td>
          <td>{{ emp.timeResult }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
    {% else %}
    <p class="empty">该范围内没有员工在 18:30~18:45 之间打下班卡</p>
    {% endif %}
  </div>
</div>
{% endif %}

{% if csv_path %}
<div class="card">
  <a class="btn btn-primary" href="/download-csv" style="text-decoration:none">下载 CSV 报表</a>
</div>
{% endif %}

{% else %}
<div class="card">
  <p class="empty">暂无报告数据，请选择日期后点击上方按钮</p>
</div>
{% endif %}

<div class="card">
  <div class="card-title">排除说明</div>
  <p class="meta">1. 18:30 前打下班卡的排除</p>
  <p class="meta">2. 客服部普通员工排除（保留客服主管）</p>
  <p class="meta">3. 指定人员排除（郭松生、谢辉）</p>
</div>

<form method="POST" action="/logout">
  <button class="btn" style="background:#eee;color:#666" type="submit">退出登录</button>
</form>

{% endif %}

<script>
function switchMode(mode) {
  var tabs = document.querySelectorAll('.tab');
  tabs.forEach(function(t, i) {
    t.classList.toggle('active', (mode === 'single' && i === 0) || (mode === 'period' && i === 1));
  });
  document.getElementById('singleMode').style.display = (mode === 'single') ? 'block' : 'none';
  document.getElementById('periodMode').style.display = (mode === 'period') ? 'block' : 'none';
  var modeInput = document.getElementById('queryMode');
  if (modeInput) modeInput.value = mode;
}

function toggleAggregatePanel() {
  var panel = document.getElementById('aggregatePanel');
  if (!panel) return;
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

function toggleResultPanel(button) {
  var panel = document.getElementById('resultPanel');
  if (!panel) return;
  var collapsed = panel.style.display === 'none';
  panel.style.display = collapsed ? 'block' : 'none';
  if (button) button.textContent = collapsed ? '收起' : '展开';
}

function switchAggregate(mode, button) {
  ['person', 'department', 'position', 'detail'].forEach(function(name) {
    var section = document.getElementById('aggregate-' + name);
    if (section) section.style.display = (name === mode) ? 'block' : 'none';
  });
  document.querySelectorAll('.aggregate-tab').forEach(function(tab) {
    tab.classList.remove('active');
  });
  if (button) button.classList.add('active');
}

// 快捷日期以服务端业务日期为基准，避免浏览器 UTC 时区导致日期少一天。
var serverToday = "{{ today }}";

function parseDateValue(value) {
  var parts = value.split('-').map(Number);
  // 使用本地构造函数，避免 new Date('yyyy-mm-dd') 按 UTC 解析。
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function fmt(d) {
  var m = String(d.getMonth() + 1).padStart(2, '0');
  var day = String(d.getDate()).padStart(2, '0');
  return d.getFullYear() + '-' + m + '-' + day;
}

function setDate(daysAgo) {
  var d = parseDateValue(serverToday);
  d.setDate(d.getDate() - daysAgo);
  document.getElementById('dateInput').value = fmt(d);
  switchMode('single');
}

function setPeriod(days) {
  var end = parseDateValue(serverToday);
  var start = parseDateValue(serverToday);
  start.setDate(start.getDate() - days);
  document.getElementById('startInput').value = fmt(start);
  document.getElementById('endInput').value = fmt(end);
  switchMode('period');
}

// 切换到周期模式时，若结束日期为空则自动填今天
document.getElementById('endInput')?.addEventListener('focus', function() {
  if (!this.value) this.value = document.getElementById('startInput').value;
});

// 后台查询可能超过 1.5 秒。页面返回后轮询状态，完成后自动刷新，
// 避免按钮一直停留在“正在执行”。
{% if authenticated and status.running %}
(function pollRunStatus() {
  var timer = setInterval(function() {
    fetch('/api/status', { cache: 'no-store' })
      .then(function(response) { return response.json(); })
      .then(function(status) {
        if (!status.running) {
          clearInterval(timer);
          window.location.reload();
        }
      })
      .catch(function() {
        // 网络短暂失败时继续轮询，不影响后台任务。
      });
  }, 2000);
})();
{% endif %}
</script>

</body>
</html>"""


def _resolve_query_range(form_data) -> tuple[str, str]:
    """根据当前查询模式解析日期范围，避免隐藏输入框污染提交参数。"""
    mode = form_data.get("mode", "").strip().lower()
    date_str = form_data.get("date", "").strip()
    start_str = form_data.get("start", "").strip()
    end_str = form_data.get("end", "").strip()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if mode == "single":
        target = date_str or today_str
        return target, target

    if mode == "period":
        # 周期模式允许只填写一端，另一端使用同一天，避免提交空范围。
        target_start = start_str or end_str or date_str or today_str
        target_end = end_str or start_str or date_str or today_str
        if target_end < target_start:
            target_start, target_end = target_end, target_start
        return target_start, target_end

    # 兼容没有 mode 字段的旧请求。
    if start_str and end_str:
        if end_str < start_str:
            start_str, end_str = end_str, start_str
        return start_str, end_str
    target = date_str or today_str
    return target, target


def _render(authenticated: bool, report=None):
    """统一渲染页面。"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    if report is None:
        report = _load_latest_report()

    csv_path = _load_latest_csv_path(report)

    # 判断当前是单日还是周期
    is_period = bool(report and report.get("isPeriod"))

    selected_date = today_str
    selected_start = today_str
    selected_end = today_str
    if report:
        selected_date = report.get("startDate") or report.get("queryDate") or today_str
        selected_start = report.get("startDate") or today_str
        selected_end = report.get("endDate") or today_str
        is_period = report.get("isPeriod", False)

    return render_template_string(
        PAGE_TEMPLATE,
        authenticated=authenticated,
        status=_last_run_status,
        report=report,
        csv_path=csv_path,
        is_period=is_period,
        selected_date=selected_date,
        selected_start=selected_start,
        selected_end=selected_end,
        today=today_str,
        yesterday=yesterday_str,
    )


@app.route("/", methods=["GET"])
def index():
    """主页。"""
    authenticated = request.args.get("auth") == "1" or request.cookies.get("hr_auth") == "1"
    return _render(authenticated)


@app.route("/login", methods=["POST"])
def login():
    """PIN 码验证。"""
    pin = request.form.get("pin", "").strip()
    if pin == config.WEB_ACCESS_PIN:
        resp = app.make_response(_render(True))
        resp.set_cookie("hr_auth", "1", max_age=86400)  # 24小时有效
        return resp
    return _render(False)


@app.route("/trigger", methods=["GET", "POST"])
def trigger():
    """触发考勤拉取（支持单日或周期）。"""
    # 防止用户刷新 POST 后停留在 /trigger 页面。
    if request.method == "GET":
        return redirect("/")

    if request.cookies.get("hr_auth") != "1":
        return _render(False)

    # 解析查询范围，只使用当前模式对应的日期字段。
    target_start, target_end = _resolve_query_range(request.form)

    if _last_run_status["running"]:
        _last_run_status["message"] = "正在执行中，请稍候..."
    else:
        # 先同步更新状态，避免后台线程尚未启动时首页误显示“就绪”。
        _last_run_status["running"] = True
        _last_run_status["message"] = f"正在查询 {target_start} ~ {target_end} ..."
        thread = threading.Thread(
            target=_do_attendance_check_async,
            args=(target_start, target_end),
            daemon=True,
        )
        thread.start()

    # 使用 PRG（Post/Redirect/Get）避免页面地址停留在 /trigger。
    return redirect("/")


@app.route("/download-csv", methods=["GET"])
def download_csv():
    """下载与当前报表对应的 CSV 文件。"""
    from flask import send_file

    if request.cookies.get("hr_auth") != "1":
        return _render(True)

    csv_path = _load_latest_csv_path(_load_latest_report())
    if csv_path and os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True, download_name=os.path.basename(csv_path))
    return "暂无 CSV 文件", 404


@app.route("/logout", methods=["POST"])
def logout():
    """退出登录。"""
    resp = app.make_response(_render(False))
    resp.delete_cookie("hr_auth")
    return resp


@app.route("/api/status", methods=["GET"])
def api_status():
    """API 端点：获取执行状态。"""
    return jsonify(_last_run_status)


@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    """API 端点：触发执行（需要 token）。"""
    token = request.headers.get("X-API-Token", "")
    if token != config.WEB_ACCESS_PIN:
        return jsonify({"error": "unauthorized"}), 401

    if _last_run_status["running"]:
        return jsonify({"status": "already_running"})

    start_date = request.args.get("start", "") or None
    end_date = request.args.get("end", "") or None

    thread = threading.Thread(
        target=_do_attendance_check_async,
        args=(start_date, end_date),
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "triggered", "start": start_date, "end": end_date})


@app.route("/api/report", methods=["GET"])
def api_report():
    """API 端点：获取最新报告。"""
    token = request.headers.get("X-API-Token", "")
    if token != config.WEB_ACCESS_PIN:
        return jsonify({"error": "unauthorized"}), 401

    start_date = request.args.get("start")
    end_date = request.args.get("end")

    if start_date and end_date:
        from reporter import load_report_for_period
        report = load_report_for_period(start_date, end_date)
    else:
        report = _load_latest_report()

    if report:
        return jsonify(report)
    return jsonify({"error": "no_report"}), 404


def start_web_server(port: int = None):
    """启动 Web 服务。"""
    port = port or config.WEB_PORT
    print(f"\n[Web] 人事审核页面已启动: http://0.0.0.0:{port}")
    print(f"[Web] 访问 PIN 码: {config.WEB_ACCESS_PIN}")
    print(f"[Web] 手机访问: http://<服务器IP>:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)

"""
钉钉 API 客户端封装
负责：
  - 获取/缓存 access_token
  - 获取部门列表 + 用户列表 + 用户详情（姓名）
  - 获取打卡结果
"""

import json
import time
import requests
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


class DingTalkClient:
    """钉钉 API 客户端，自动管理 access_token 缓存与刷新。"""

    def __init__(self):
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        # 钉钉接口偶发 ReadTimeout，统一为只读 GET/POST 请求配置有限重试。
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
        adapter = HTTPAdapter(max_retries=retry)
        self._session = requests.Session()
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        # userId -> name 的缓存，避免重复调用
        self._user_name_cache: dict[str, str] = {}
        # userId -> 用户详情缓存（部门、职位等元数据）
        self._user_profile_cache: dict[str, dict] = {}
        # dept_id -> dept_detail 的缓存
        self._dept_detail_cache: dict[int, dict] = {}
        # 部门树和部门用户列表在一次执行中复用，避免重复调用触发钉钉 QPS 限流。
        self._all_department_ids_cache: list[int] | None = None
        self._dept_user_ids_cache: dict[int, list[str]] = {}
        self._all_user_ids_cache: list[str] | None = None
        self._load_cached_token()

    def _post_json_with_api_retry(self, url: str, payload: dict) -> dict:
        """发送钉钉 POST 请求，并对业务层限流错误进行退避重试。"""
        token = self.get_access_token()
        last_data: dict = {}
        for attempt in range(config.DINGTALK_REQUEST_RETRIES + 1):
            response = self._session.post(
                url,
                params={"access_token": token},
                json=payload,
                timeout=config.DINGTALK_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            last_data = data
            errcode = data.get("errcode", 0)
            if errcode == 0:
                return data

            # 90002 是钉钉业务层 QPS 限流，HTTPAdapter 无法识别，需要主动等待。
            if errcode in {90002, 88, 429} and attempt < config.DINGTALK_REQUEST_RETRIES:
                wait_seconds = config.DINGTALK_API_RETRY_WAIT_SECONDS * (attempt + 1)
                print(
                    f"[DingTalk] 接口被限流 (errcode={errcode})，"
                    f"{wait_seconds:g}s 后重试 ({attempt + 1}/"
                    f"{config.DINGTALK_REQUEST_RETRIES}) ..."
                )
                time.sleep(wait_seconds)
                continue
            return data

        return last_data

    # ------------------------------------------------------------------ #
    #  access_token 管理
    # ------------------------------------------------------------------ #

    def _load_cached_token(self):
        """从本地缓存文件加载 access_token（如果未过期）。"""
        try:
            with open(config.TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("expires_at", 0) > time.time() + 300:
                self._access_token = data["access_token"]
                self._token_expires_at = data["expires_at"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def _save_cached_token(self):
        """将 access_token 缓存到本地文件。"""
        with open(config.TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "access_token": self._access_token,
                "expires_at": self._token_expires_at,
            }, f)

    def get_access_token(self) -> str:
        """获取有效的 access_token，缓存未过期则直接返回。"""
        if self._access_token and self._token_expires_at > time.time() + 300:
            return self._access_token

        print("[DingTalk] 正在获取 access_token ...")
        resp = self._session.get(config.TOKEN_URL, params={
            "appkey": config.DINGTALK_APP_KEY,
            "appsecret": config.DINGTALK_APP_SECRET,
        }, timeout=config.DINGTALK_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("errcode") != 0:
            raise RuntimeError(f"获取 access_token 失败: {data.get('errmsg')}")

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 7200)
        self._save_cached_token()
        print(f"[DingTalk] access_token 获取成功，有效期 {data.get('expires_in', 7200)}s")
        return self._access_token

    # ------------------------------------------------------------------ #
    #  部门相关
    # ------------------------------------------------------------------ #

    def get_all_department_ids(self) -> list[int]:
        """递归获取企业下所有部门 ID（含根部门）。"""
        if self._all_department_ids_cache is not None:
            return list(self._all_department_ids_cache)

        all_ids = [config.ROOT_DEPARTMENT_ID]
        queue = [config.ROOT_DEPARTMENT_ID]

        while queue:
            parent_id = queue.pop(0)
            data = self._post_json_with_api_retry(
                config.DEPARTMENT_LIST_URL,
                {"dept_id": parent_id},
            )

            if data.get("errcode") != 0:
                raise RuntimeError(
                    f"获取子部门失败 (parent={parent_id}): {data.get('errmsg')}"
                )

            sub_depts = data.get("result", [])
            for dept in sub_depts:
                dept_id = dept["dept_id"]
                all_ids.append(dept_id)
                queue.append(dept_id)

        self._all_department_ids_cache = list(dict.fromkeys(all_ids))
        print(f"[DingTalk] 共获取 {len(self._all_department_ids_cache)} 个部门")
        return list(self._all_department_ids_cache)

    def get_department_detail(self, dept_id: int) -> dict:
        """
        获取部门详情，含主管 userId 列表 (dept_manager_userid_list)。
        结果会缓存。
        """
        if dept_id in self._dept_detail_cache:
            return self._dept_detail_cache[dept_id]

        data = self._post_json_with_api_retry(
            config.DEPARTMENT_DETAIL_URL,
            {"dept_id": dept_id},
        )

        if data.get("errcode") != 0:
            print(f"[DingTalk] 获取部门详情失败 (dept={dept_id}): {data.get('errmsg')}")
            return {}

        result = data.get("result", {})
        self._dept_detail_cache[dept_id] = result
        return result

    def get_department_managers(self, dept_id: int) -> list[str]:
        """获取部门主管 userId 列表。"""
        detail = self.get_department_detail(dept_id)
        return detail.get("dept_manager_userid_list", [])

    def find_customer_service_dept_ids(self) -> list[int]:
        """查找名称包含"客服"关键词的部门 ID 列表。"""
        all_dept_ids = self.get_all_department_ids()
        cs_dept_ids = []

        for dept_id in all_dept_ids:
            detail = self.get_department_detail(dept_id)
            dept_name = detail.get("name", "")
            for keyword in config.CUSTOMER_SERVICE_DEPT_KEYWORDS:
                if keyword in dept_name:
                    cs_dept_ids.append(dept_id)
                    print(f"[DingTalk] 客服部门: {dept_name} (ID={dept_id})")
                    break

        return cs_dept_ids

    def get_customer_service_excluded_user_ids(self) -> set[str]:
        """
        获取需要排除的客服部普通员工 userId（保留客服主管）。
        返回排除集合：客服部所有人员 - 客服主管。
        """
        cs_dept_ids = self.find_customer_service_dept_ids()
        if not cs_dept_ids:
            return set()

        # 收集所有客服主管 userId
        cs_managers: set[str] = set()
        for dept_id in cs_dept_ids:
            managers = self.get_department_managers(dept_id)
            cs_managers.update(managers)

        # 收集客服部所有人员
        cs_all_users: set[str] = set()
        for dept_id in cs_dept_ids:
            users = self.get_user_ids_by_department(dept_id)
            cs_all_users.update(users)

        # 排除 = 全部 - 主管
        excluded = cs_all_users - cs_managers
        print(f"[DingTalk] 客服部总人数: {len(cs_all_users)}, 主管: {len(cs_managers)}, "
              f"排除普通员工: {len(excluded)}")

        # 打印保留的主管
        for mgr_id in cs_managers:
            mgr_name = self.get_user_name(mgr_id)
            print(f"[DingTalk] 保留客服主管: {mgr_name} ({mgr_id})")

        return excluded

    # ------------------------------------------------------------------ #
    #  用户相关
    # ------------------------------------------------------------------ #

    def get_user_ids_by_department(self, dept_id: int) -> list[str]:
        """获取指定部门下所有用户的 userId（分页获取）。"""
        if dept_id in self._dept_user_ids_cache:
            return list(self._dept_user_ids_cache[dept_id])

        user_ids: list[str] = []
        cursor = 0
        size = 100

        while True:
            data = self._post_json_with_api_retry(
                config.USER_LIST_ID_URL,
                {"dept_id": dept_id, "cursor": cursor, "size": size},
            )

            if data.get("errcode") != 0:
                raise RuntimeError(
                    f"获取部门用户失败 (dept={dept_id}): {data.get('errmsg')}"
                )

            result = data.get("result", {})
            # 钉钉该接口实际返回字段是 userid_list（没有下划线），
            # 部分旧文档/旧响应曾写成 user_id_list，这里兼容两种格式。
            user_ids.extend(
                result.get("userid_list")
                or result.get("user_id_list", [])
            )

            if not result.get("has_more", False):
                break
            cursor = result.get("next_cursor", 0)

        self._dept_user_ids_cache[dept_id] = list(dict.fromkeys(user_ids))
        return list(self._dept_user_ids_cache[dept_id])

    def get_all_user_ids(self) -> list[str]:
        """获取企业所有部门的用户 userId 去重列表。"""
        if self._all_user_ids_cache is not None:
            return list(self._all_user_ids_cache)

        dept_ids = self.get_all_department_ids()
        all_user_ids: set[str] = set()

        for dept_id in dept_ids:
            ids = self.get_user_ids_by_department(dept_id)
            all_user_ids.update(ids)

        self._all_user_ids_cache = list(all_user_ids)
        print(f"[DingTalk] 共获取 {len(self._all_user_ids_cache)} 个用户")
        return list(self._all_user_ids_cache)

    def get_user_profile(self, user_id: str) -> dict:
        """获取用户详情，包含姓名、部门 ID 和职位标题。"""
        if user_id in self._user_profile_cache:
            return self._user_profile_cache[user_id]

        data = self._post_json_with_api_retry(
            config.USER_DETAIL_URL,
            {"userid": user_id},
        )
        if data.get("errcode") != 0:
            profile = {"userid": user_id, "name": user_id}
        else:
            profile = data.get("result", {}) or {"userid": user_id}
            profile.setdefault("userid", user_id)

        self._user_profile_cache[user_id] = profile
        self._user_name_cache[user_id] = profile.get("name", user_id)
        return profile

    def get_user_name(self, user_id: str) -> str:
        """获取用户姓名（带缓存）。"""
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]
        return self.get_user_profile(user_id).get("name", user_id)

    def get_user_metadata(self, user_id: str) -> dict[str, str]:
        """返回用于报表聚合的人员元数据：姓名、部门、职位。"""
        profile = self.get_user_profile(user_id)
        department_names: list[str] = []
        for dept_id in profile.get("dept_id_list") or []:
            detail = self.get_department_detail(int(dept_id))
            dept_name = detail.get("name")
            if dept_name and dept_name not in department_names:
                department_names.append(dept_name)

        return {
            "userName": profile.get("name", user_id),
            "department": "、".join(department_names) or "未填写",
            "position": profile.get("title") or profile.get("position") or "未填写",
        }

    def get_all_user_names(self, user_ids: list[str]) -> dict[str, str]:
        """批量获取用户姓名映射。"""
        name_map: dict[str, str] = {}
        for uid in user_ids:
            name_map[uid] = self.get_user_name(uid)
            time.sleep(0.05)
        return name_map

    def find_user_ids_by_names(self, names: list[str]) -> set[str]:
        """
        通过姓名查找对应的 userId。
        遍历所有用户详情来匹配。
        """
        all_user_ids = self.get_all_user_ids()
        matched: set[str] = set()
        remaining_names = set(names)

        for uid in all_user_ids:
            if not remaining_names:
                break
            user_name = self.get_user_name(uid)
            if user_name in remaining_names:
                matched.add(uid)
                remaining_names.discard(user_name)
                print(f"[DingTalk] 按姓名排除: {user_name} ({uid})")

        return matched

    # ------------------------------------------------------------------ #
    #  考勤打卡记录
    # ------------------------------------------------------------------ #

    def get_attendance_records(
        self,
        user_ids: list[str],
        work_date_from: str,
        work_date_to: str,
    ) -> list[dict]:
        """
        获取指定用户列表在指定日期范围内的打卡结果。
        - work_date_from / work_date_to: "yyyy-MM-dd HH:mm:ss"
        - userIdList 每次最多 50 个
        """
        token = self.get_access_token()
        all_records: list[dict] = []

        for i in range(0, len(user_ids), config.ATTENDANCE_BATCH_SIZE):
            batch = user_ids[i:i + config.ATTENDANCE_BATCH_SIZE]
            offset = 0

            while True:
                resp = self._session.post(
                    config.ATTENDANCE_LIST_URL,
                    params={"access_token": token},
                    json={
                        "workDateFrom": work_date_from,
                        "workDateTo": work_date_to,
                        "userIdList": batch,
                        "offset": offset,
                        "limit": config.ATTENDANCE_LIMIT,
                    },
                    timeout=config.DINGTALK_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("errcode") != 0:
                    print(f"[DingTalk] 获取考勤记录失败: {data.get('errmsg')}")
                    break

                records = data.get("recordresult", [])
                all_records.extend(records)

                # 当前接口返回 camelCase 的 hasMore，兼容旧响应中的 has_more。
                has_more = data.get("hasMore", data.get("has_more", False))
                if has_more:
                    offset += config.ATTENDANCE_LIMIT
                else:
                    break

            time.sleep(0.2)

        print(f"[DingTalk] 共获取 {len(all_records)} 条打卡记录")
        return all_records

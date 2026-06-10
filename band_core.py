import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
try:
    import holidays as pyholidays
except Exception:
    pyholidays = None
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

KST = ZoneInfo("Asia/Seoul")
APP_NAME = "Band Auto Writer"
PROJECT_DB_PREFIX = "baw_"


def prefixed(name: str) -> str:
    return f"{PROJECT_DB_PREFIX}{name}"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///band_writer.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+pg8000://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+pg8000://", 1)
    return url


DATABASE_URL = database_url()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, connect_args=connect_args)
metadata = MetaData()

settings_table = Table(prefixed("settings"), metadata, Column("id", Integer, primary_key=True), Column("user_id", String(80)), Column("data", Text, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
logs_table = Table(prefixed("logs"), metadata, Column("id", Integer, primary_key=True, autoincrement=True), Column("user_id", String(80)), Column("level", String(20), nullable=False), Column("message", Text, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
run_history_table = Table(prefixed("run_history"), metadata, Column("id", Integer, primary_key=True, autoincrement=True), Column("user_id", String(80)), Column("run_key", String(220), nullable=False), Column("status", String(30), nullable=False), Column("detail", Text), Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("run_key", name="uq_baw_run_history_run_key"))
runtime_table = Table(prefixed("runtime_status"), metadata, Column("key", String(80), primary_key=True), Column("value", Text, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))

DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False, "times": ["08:40"], "band_access_token": "", "band_key": "", "band_name": "", "dcu_count": 1, "modem_counts": [1, 1, 1, 1],
    "date_format": "%Y년 %m월 %d일", "modem_template": "{date} 모뎀 {team}조 {members}", "dcu_template": "{date} DCU {team}조 {members}", "tail_templates": ["{date} TBM"],
    "modem_zone_order": "desc", "modem_team_order": "desc", "dcu_team_order": "desc", "post_interval_seconds": 10, "retry_interval_seconds": 20, "retry_limit": 0, "do_push": False,
    "skip_saturday": False, "skip_korean_holidays": False, "skip_extra_dates": False, "extra_skip_dates": [], "team_members": {"modem": {}, "dcu": {}},
}

class BandOpenApi:
    def token_from_settings(self, settings: Optional[Dict[str, Any]] = None) -> str:
        if settings:
            token = str(settings.get("band_access_token", "")).strip()
            if token:
                return token
        return os.environ.get("BAND_ACCESS_TOKEN", "").strip()
    def is_ready(self, settings: Optional[Dict[str, Any]] = None) -> bool:
        return bool(self.token_from_settings(settings))
    def _api_call(self, api_path: str, params: Optional[dict] = None, method: str = "get", access_token: str = "") -> dict:
        token = access_token.strip() or os.environ.get("BAND_ACCESS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BAND Access Token이 설정되어 있지 않습니다.")
        params = dict(params or {}); params["access_token"] = token
        url = f"https://openapi.band.us{api_path}"
        response = requests.get(url, params=params, timeout=20) if method == "get" else requests.post(url, data=params, timeout=20)
        if not response.ok:
            raise RuntimeError(f"BAND API 호출 실패: {response.status_code}, {response.text[:500]}")
        data = response.json()
        if data.get("result_code") != 1:
            raise RuntimeError(f"BAND API 오류: {data}")
        return data.get("result_data", {})
    def get_bands(self, access_token: str = "") -> List[dict]:
        return self._api_call("/v2.1/bands", access_token=access_token).get("bands", [])
    def create_post(self, band_key: str, content: str, do_push: bool = False, access_token: str = "") -> dict:
        params = {"band_key": band_key, "content": content}
        if do_push:
            params["do_push"] = "true"
        return self._api_call("/v2.2/band/post/create", params=params, method="post", access_token=access_token)

api = BandOpenApi()

def now_kst() -> datetime:
    return datetime.now(tz=KST)

def migrate_legacy_tables_if_needed() -> None:
    pairs = [("settings", settings_table.name), ("logs", logs_table.name), ("run_history", run_history_table.name), ("runtime_status", runtime_table.name)]
    inspector = inspect(engine)
    with engine.begin() as conn:
        for old_name, new_name in pairs:
            try:
                if not inspector.has_table(old_name) or not inspector.has_table(new_name):
                    continue
                old_count = conn.execute(text(f'SELECT COUNT(*) FROM "{old_name}"')).scalar() or 0
                new_count = conn.execute(text(f'SELECT COUNT(*) FROM "{new_name}"')).scalar() or 0
                if old_count > 0 and new_count == 0:
                    conn.execute(text(f'INSERT INTO "{new_name}" SELECT * FROM "{old_name}"'))
            except Exception as exc:
                print(f"legacy migration skipped for {old_name}: {exc}", flush=True)

def ensure_column(table_name: str, column_name: str, definition: str) -> None:
    inspector = inspect(engine)
    if column_name in [column["name"] for column in inspector.get_columns(table_name)]:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {definition}'))

def init_db() -> None:
    metadata.create_all(engine)
    for table in (settings_table, logs_table, run_history_table):
        ensure_column(table.name, "user_id", "VARCHAR(80)")
    migrate_legacy_tables_if_needed()
    with engine.begin() as conn:
        if conn.execute(select(settings_table.c.id).where(settings_table.c.id == 1)).first() is None:
            conn.execute(insert(settings_table).values(id=1, data=json.dumps(DEFAULT_SETTINGS, ensure_ascii=False), updated_at=now_kst()))

def add_log(level: str, message: str, user_id: Optional[str] = None) -> None:
    with engine.begin() as conn:
        conn.execute(insert(logs_table).values(user_id=user_id, level=level, message=str(message), created_at=now_kst()))
        old_ids = [r[0] for r in conn.execute(select(logs_table.c.id).order_by(logs_table.c.id.desc()).offset(300))]
        if old_ids:
            conn.execute(logs_table.delete().where(logs_table.c.id.in_(old_ids)))

def get_logs(limit: int = 80, user_id: Optional[str] = None) -> List[dict]:
    with engine.begin() as conn:
        query = select(logs_table).order_by(logs_table.c.id.desc()).limit(limit)
        if user_id:
            query = query.where(logs_table.c.user_id == user_id)
        rows = conn.execute(query).mappings().all()
    return [dict(r) for r in rows]

def set_runtime(key: str, value: Any) -> None:
    text_value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool, int, float)) else str(value)
    with engine.begin() as conn:
        exists = conn.execute(select(runtime_table.c.key).where(runtime_table.c.key == key)).first()
        if exists:
            conn.execute(update(runtime_table).where(runtime_table.c.key == key).values(value=text_value, updated_at=now_kst()))
        else:
            conn.execute(insert(runtime_table).values(key=key, value=text_value, updated_at=now_kst()))

def get_runtime_values() -> Dict[str, Dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(select(runtime_table)).mappings().all()
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        value: Any = row["value"]
        try: value = json.loads(value)
        except Exception: pass
        result[row["key"]] = {"value": value, "updated_at": row["updated_at"]}
    return result

def normalize_hhmm(raw: str) -> str:
    raw = raw.strip()
    if not re.match(r"^\d{1,2}:\d{2}$", raw): raise ValueError(f"시간 형식이 올바르지 않습니다: {raw}")
    hour, minute = [int(x) for x in raw.split(":")]
    if not (0 <= hour <= 23 and 0 <= minute <= 59): raise ValueError(f"시간 범위가 올바르지 않습니다: {raw}")
    return f"{hour:02d}:{minute:02d}"

def parse_times(raw: Any) -> List[str]:
    candidates = raw if isinstance(raw, list) else str(raw or "").replace("\n", ",").split(",")
    times = [normalize_hhmm(str(x)) for x in candidates if str(x).strip()]
    if not times: raise ValueError("글 작성 시간을 1개 이상 입력하세요.")
    return sorted(set(times))

def parse_int(value: Any, name: str, minimum: int = 0, maximum: int = 999) -> int:
    try: parsed = int(str(value).strip())
    except Exception as exc: raise ValueError(f"{name}은 숫자로 입력하세요.") from exc
    if parsed < minimum or parsed > maximum: raise ValueError(f"{name}은 {minimum}~{maximum} 사이로 입력하세요.")
    return parsed

def normalize_order(value: Any) -> str:
    return "asc" if str(value).lower() == "asc" else "desc"

def parse_date_list(raw: Any) -> List[str]:
    candidates = raw if isinstance(raw, list) else re.split(r"[,\n\s]+", str(raw or ""))
    result: List[str] = []
    for item in candidates:
        value = str(item).strip()
        if not value: continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", value): raise ValueError(f"제외일 날짜 형식이 올바르지 않습니다: {value} (예: 2026-08-17)")
        datetime.strptime(value, "%Y-%m-%d"); result.append(value)
    return sorted(set(result))

def parse_member_names(raw: Any) -> List[str]:
    names = [str(item).strip() for item in re.split(r"[/,\n]+", str(raw or "")) if str(item).strip()]
    if len(names) > 4:
        raise ValueError("한 조에는 최대 4명까지만 입력할 수 있습니다.")
    return names

def normalize_team_members(raw: Any) -> Dict[str, Dict[str, List[str]]]:
    source = raw if isinstance(raw, dict) else {}
    result: Dict[str, Dict[str, List[str]]] = {"modem": {}, "dcu": {}}
    for group in ("modem", "dcu"):
        group_data = source.get(group, {})
        if not isinstance(group_data, dict):
            continue
        for key, names in group_data.items():
            parsed = names if isinstance(names, list) else parse_member_names(names)
            cleaned = [str(name).strip() for name in parsed if str(name).strip()]
            if len(cleaned) > 4:
                raise ValueError("한 조에는 최대 4명까지만 입력할 수 있습니다.")
            if cleaned:
                result[group][str(key)] = cleaned
    return result

def normalize_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS); merged.update(data or {})
    modem_counts = merged.get("modem_counts", [0,0,0,0])
    if not isinstance(modem_counts, list): modem_counts = [0,0,0,0]
    modem_counts = (modem_counts + [0,0,0,0])[:4]
    tail_templates = merged.get("tail_templates", [])
    if isinstance(tail_templates, str): tail_templates = [line.strip() for line in tail_templates.splitlines() if line.strip()]
    if not isinstance(tail_templates, list): tail_templates = []
    return {"enabled": bool(merged.get("enabled")), "times": parse_times(merged.get("times")), "band_access_token": str(merged.get("band_access_token","")).strip(), "band_key": str(merged.get("band_key","")).strip(), "band_name": str(merged.get("band_name","")).strip(), "dcu_count": parse_int(merged.get("dcu_count",0), "DCU 조 수", 0), "modem_counts": [parse_int(v, f"{i+1}권역 모뎀 조 수", 0) for i,v in enumerate(modem_counts)], "date_format": str(merged.get("date_format") or DEFAULT_SETTINGS["date_format"]).strip(), "modem_template": str(merged.get("modem_template") or DEFAULT_SETTINGS["modem_template"]).strip(), "dcu_template": str(merged.get("dcu_template") or DEFAULT_SETTINGS["dcu_template"]).strip(), "tail_templates": [str(x).strip() for x in tail_templates if str(x).strip()], "modem_zone_order": normalize_order(merged.get("modem_zone_order")), "modem_team_order": normalize_order(merged.get("modem_team_order")), "dcu_team_order": normalize_order(merged.get("dcu_team_order")), "post_interval_seconds": parse_int(merged.get("post_interval_seconds",10), "성공 후 대기초", 0, 3600), "retry_interval_seconds": parse_int(merged.get("retry_interval_seconds",20), "실패 후 재시도 대기초", 1, 3600), "retry_limit": parse_int(merged.get("retry_limit",0), "재시도 제한", 0, 100), "do_push": bool(merged.get("do_push")), "skip_saturday": bool(merged.get("skip_saturday")), "skip_korean_holidays": bool(merged.get("skip_korean_holidays")), "skip_extra_dates": bool(merged.get("skip_extra_dates")), "extra_skip_dates": parse_date_list(merged.get("extra_skip_dates", [])), "team_members": normalize_team_members(merged.get("team_members", {}))}

def form_to_settings(form: Any, enabled: bool) -> Dict[str, Any]:
    band_value = form.get("band_key", "").strip(); band_key, band_name = band_value, form.get("band_name", "").strip()
    if "|" in band_value:
        band_key, band_name_from_value = band_value.split("|", 1); band_name = band_name or band_name_from_value
    team_members: Dict[str, Dict[str, List[str]]] = {"modem": {}, "dcu": {}}
    for key in form.keys():
        if key.startswith("members_modem_"):
            team_key = key.replace("members_modem_", "", 1)
            names = parse_member_names(form.get(key, ""))
            if names:
                team_members["modem"][team_key] = names
        elif key.startswith("members_dcu_"):
            team_key = key.replace("members_dcu_", "", 1)
            names = parse_member_names(form.get(key, ""))
            if names:
                team_members["dcu"][team_key] = names
    return {"enabled": enabled, "times": form.get("times", ""), "band_access_token": form.get("band_access_token", ""), "band_key": band_key, "band_name": band_name, "dcu_count": form.get("dcu_count", 0), "modem_counts": [form.get(f"modem_{i}", 0) for i in range(1,5)], "date_format": form.get("date_format", DEFAULT_SETTINGS["date_format"]), "modem_template": form.get("modem_template", DEFAULT_SETTINGS["modem_template"]), "dcu_template": form.get("dcu_template", DEFAULT_SETTINGS["dcu_template"]), "tail_templates": form.get("tail_templates", ""), "modem_zone_order": form.get("modem_zone_order", "desc"), "modem_team_order": form.get("modem_team_order", "desc"), "dcu_team_order": form.get("dcu_team_order", "desc"), "post_interval_seconds": form.get("post_interval_seconds", 10), "retry_interval_seconds": form.get("retry_interval_seconds", 20), "retry_limit": form.get("retry_limit", 0), "do_push": form.get("do_push") == "on" or form.get("do_push") is True, "skip_saturday": form.get("skip_saturday") == "on" or form.get("skip_saturday") is True, "skip_korean_holidays": form.get("skip_korean_holidays") == "on" or form.get("skip_korean_holidays") is True, "skip_extra_dates": form.get("skip_extra_dates") == "on" or form.get("skip_extra_dates") is True, "extra_skip_dates": form.get("extra_skip_dates", ""), "team_members": team_members}

def get_settings(user_id: Optional[str] = None) -> Dict[str, Any]:
    with engine.begin() as conn:
        if user_id:
            row = conn.execute(select(settings_table.c.data).where(settings_table.c.user_id == user_id)).first()
            if row is None:
                conn.execute(insert(settings_table).values(user_id=user_id, data=json.dumps(DEFAULT_SETTINGS, ensure_ascii=False), updated_at=now_kst()))
                row = conn.execute(select(settings_table.c.data).where(settings_table.c.user_id == user_id)).first()
        else:
            row = conn.execute(select(settings_table.c.data).where(settings_table.c.id == 1)).first()
    data = dict(DEFAULT_SETTINGS)
    if row:
        try:
            saved = json.loads(row[0])
            if isinstance(saved, dict): data.update(saved)
        except json.JSONDecodeError: add_log("ERROR", "저장된 설정 JSON을 읽을 수 없어 기본값으로 대체했습니다.")
    return normalize_settings(data)

def save_settings(data: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    normalized = normalize_settings(data)
    with engine.begin() as conn:
        if user_id:
            result = conn.execute(update(settings_table).where(settings_table.c.user_id == user_id).values(data=json.dumps(normalized, ensure_ascii=False), updated_at=now_kst()))
            if result.rowcount == 0:
                conn.execute(insert(settings_table).values(user_id=user_id, data=json.dumps(normalized, ensure_ascii=False), updated_at=now_kst()))
        else:
            conn.execute(update(settings_table).where(settings_table.c.id == 1).values(data=json.dumps(normalized, ensure_ascii=False), updated_at=now_kst()))
    return normalized

def get_all_enabled_settings() -> List[Dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(select(settings_table.c.user_id, settings_table.c.data)).mappings().all()
    result: List[Dict[str, Any]] = []
    for row in rows:
        try:
            settings = normalize_settings(json.loads(row["data"]))
        except Exception as exc:
            add_log("ERROR", f"저장된 설정을 읽을 수 없습니다: {exc}", row.get("user_id"))
            continue
        if settings.get("enabled"):
            settings["user_id"] = row.get("user_id")
            result.append(settings)
    return result

def supabase_account_settings_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL", "").strip() and os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip())


def load_local_settings_data(user_id: Optional[str] = None) -> Dict[str, Any]:
    with engine.begin() as conn:
        if user_id:
            row = conn.execute(select(settings_table.c.data).where(settings_table.c.user_id == user_id)).first()
            if row is None:
                conn.execute(insert(settings_table).values(user_id=user_id, data=json.dumps(DEFAULT_SETTINGS, ensure_ascii=False), updated_at=now_kst()))
                row = conn.execute(select(settings_table.c.data).where(settings_table.c.user_id == user_id)).first()
        else:
            row = conn.execute(select(settings_table.c.data).where(settings_table.c.id == 1)).first()
    data = dict(DEFAULT_SETTINGS)
    if row:
        try:
            saved = json.loads(row[0])
            if isinstance(saved, dict):
                data.update(saved)
        except json.JSONDecodeError:
            add_log("ERROR", "Saved settings JSON is invalid; using defaults.", user_id)
    return data


def save_local_settings_data(data: Dict[str, Any], user_id: Optional[str] = None) -> None:
    with engine.begin() as conn:
        if user_id:
            result = conn.execute(update(settings_table).where(settings_table.c.user_id == user_id).values(data=json.dumps(data, ensure_ascii=False), updated_at=now_kst()))
            if result.rowcount == 0:
                conn.execute(insert(settings_table).values(user_id=user_id, data=json.dumps(data, ensure_ascii=False), updated_at=now_kst()))
        else:
            conn.execute(update(settings_table).where(settings_table.c.id == 1).values(data=json.dumps(data, ensure_ascii=False), updated_at=now_kst()))


def get_local_settings_rows() -> List[Dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(select(settings_table.c.user_id, settings_table.c.data)).mappings().all()
    result: List[Dict[str, Any]] = []
    for row in rows:
        try:
            settings = normalize_settings(json.loads(row["data"]))
        except Exception as exc:
            add_log("ERROR", f"Saved settings cannot be loaded: {exc}", row.get("user_id"))
            continue
        settings["user_id"] = row.get("user_id")
        result.append(settings)
    return result


def get_settings(user_id: Optional[str] = None) -> Dict[str, Any]:
    data = load_local_settings_data(user_id)
    if user_id and supabase_account_settings_enabled():
        try:
            from supabase_auth import get_user_settings
            remote = get_user_settings(user_id)
            if remote:
                data.update(remote)
        except Exception as exc:
            add_log("ERROR", f"Supabase account settings load failed: {exc}", user_id)
    return normalize_settings(data)


def save_settings(data: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    normalized = normalize_settings(data)
    save_to_supabase = bool(user_id and supabase_account_settings_enabled())
    if save_to_supabase:
        try:
            from supabase_auth import get_band_access_token, save_user_settings
            if not normalized.get("band_access_token"):
                existing_token = get_band_access_token(user_id)
                if existing_token:
                    normalized["band_access_token"] = existing_token
            save_user_settings(user_id, normalized)
        except Exception as exc:
            add_log("ERROR", f"Supabase account settings save failed: {exc}", user_id)
            raise
    local_copy = dict(normalized)
    if save_to_supabase:
        local_copy["band_access_token"] = ""
    save_local_settings_data(local_copy, user_id)
    return normalized


def get_all_enabled_settings() -> List[Dict[str, Any]]:
    local_rows = get_local_settings_rows()
    local_by_user = {str(row.get("user_id") or ""): row for row in local_rows}
    if supabase_account_settings_enabled():
        try:
            from supabase_auth import get_all_user_settings
            remote_rows = get_all_user_settings()
            if remote_rows:
                result: List[Dict[str, Any]] = []
                for remote_row in remote_rows:
                    remote = dict(remote_row)
                    user_id = str(remote.pop("user_id", "") or "")
                    merged = dict(local_by_user.get(user_id) or DEFAULT_SETTINGS)
                    merged.update(remote)
                    settings = normalize_settings(merged)
                    if settings.get("enabled"):
                        settings["user_id"] = user_id
                        result.append(settings)
                return result
        except Exception as exc:
            add_log("ERROR", f"Supabase account settings list failed: {exc}")
    return [settings for settings in local_rows if settings.get("enabled")]


def build_sequence(max_count: int, order: str) -> List[int]:
    seq = list(range(1, max_count + 1)); return seq if order == "asc" else list(reversed(seq))

def render_line(template: str, context: Dict[str, Any]) -> str:
    try: return template.format(**context).strip()
    except KeyError as exc: raise ValueError(f"템플릿 변수 오류: {exc}. 사용 가능 변수는 {{date}}, {{zone}}, {{team}}, {{type}}, {{members}} 입니다.") from exc

def build_lines(settings: Dict[str, Any], base_dt: Optional[datetime] = None) -> List[str]:
    base_dt = base_dt or now_kst(); date_text = base_dt.strftime(settings["date_format"]); lines: List[str] = []
    team_members = normalize_team_members(settings.get("team_members", {}))
    for zone in build_sequence(4, settings["modem_zone_order"]):
        for team in build_sequence(settings["modem_counts"][zone - 1], settings["modem_team_order"]):
            members = "/".join(team_members["modem"].get(f"{zone}_{team}", []))
            lines.append(render_line(settings["modem_template"], {"date": date_text, "zone": zone, "team": team, "type": "모뎀", "members": members}))
    for team in build_sequence(settings["dcu_count"], settings["dcu_team_order"]):
        members = "/".join(team_members["dcu"].get(str(team), []))
        lines.append(render_line(settings["dcu_template"], {"date": date_text, "zone": "", "team": team, "type": "DCU", "members": members}))
    for template in settings["tail_templates"]: lines.append(render_line(template, {"date": date_text, "zone": "", "team": "", "type": "TBM", "members": ""}))
    return [line for line in lines if line]

def korean_holiday_name(base_dt: datetime) -> str:
    if pyholidays is None: return ""
    try:
        return str(pyholidays.country_holidays("KR", years=[base_dt.year]).get(base_dt.date()) or "")
    except Exception as exc:
        add_log("WARNING", f"공휴일 판정 실패: {exc}"); return ""

def get_skip_reason(settings: Dict[str, Any], base_dt: Optional[datetime] = None) -> str:
    base_dt = base_dt or now_kst(); date_key = base_dt.strftime("%Y-%m-%d")
    if settings.get("skip_saturday") and base_dt.weekday() == 5: return "토요일 제외 설정"
    if settings.get("skip_extra_dates") and date_key in settings.get("extra_skip_dates", []): return f"수동 제외일/임시공휴일 설정: {date_key}"
    if settings.get("skip_korean_holidays"):
        name = korean_holiday_name(base_dt)
        if name: return f"대한민국 공휴일 제외 설정: {name}"
    return ""

def acquire_run(run_key: str, user_id: Optional[str] = None) -> bool:
    try:
        with engine.begin() as conn: conn.execute(insert(run_history_table).values(user_id=user_id, run_key=run_key, status="STARTED", detail="", created_at=now_kst()))
        return True
    except IntegrityError: return False

def update_run(run_key: str, status: str, detail: str = "") -> None:
    with engine.begin() as conn: conn.execute(update(run_history_table).where(run_history_table.c.run_key == run_key).values(status=status, detail=detail))

def get_last_run(user_id: Optional[str] = None) -> Optional[dict]:
    with engine.begin() as conn:
        query = select(run_history_table).order_by(run_history_table.c.id.desc()).limit(1)
        if user_id:
            query = query.where(run_history_table.c.user_id == user_id)
        row = conn.execute(query).mappings().first()
    return dict(row) if row else None

def post_lines(settings: Dict[str, Any], lines: List[str], user_id: Optional[str] = None) -> None:
    if not settings["band_key"]: raise RuntimeError("밴드가 선택되어 있지 않습니다.")
    access_token = api.token_from_settings(settings)
    if not access_token and user_id:
        try:
            from supabase_auth import get_band_access_token
            access_token = get_band_access_token(user_id)
        except Exception as exc:
            raise RuntimeError(f"Supabase BAND Access Token 조회 실패: {exc}") from exc
    if not access_token: raise RuntimeError("BAND Access Token이 저장되어 있지 않습니다.")
    for index, line in enumerate(lines, start=1):
        attempts = 0
        while True:
            try:
                api.create_post(settings["band_key"], line, do_push=settings["do_push"], access_token=access_token); add_log("INFO", f"게시 성공 ({index}/{len(lines)}): {line}", user_id)
                if index < len(lines) and settings["post_interval_seconds"] > 0: time.sleep(settings["post_interval_seconds"])
                break
            except Exception as exc:
                attempts += 1; add_log("ERROR", f"게시 실패 ({index}/{len(lines)}, {attempts}회): {line} / {exc}", user_id)
                retry_limit = settings["retry_limit"]
                if retry_limit and attempts >= retry_limit: raise
                time.sleep(settings["retry_interval_seconds"])

def execute_schedule(scheduled_hhmm: str, manual: bool = False, user_id: Optional[str] = None) -> None:
    settings = get_settings(user_id)
    if user_id:
        try:
            from supabase_auth import get_band_access_token
            settings["band_access_token"] = get_band_access_token(user_id)
        except Exception as exc:
            add_log("ERROR", f"Supabase BAND Access Token 조회 실패: {exc}", user_id)
            if manual:
                raise
            return
    if not manual and not settings["enabled"]: return
    base_dt = now_kst(); run_key = f"{user_id or 'default'}:{'manual' if manual else 'schedule'}:{base_dt.strftime('%Y%m%d')}:{scheduled_hhmm}:{settings.get('band_key', '')}"
    if not acquire_run(run_key, user_id): add_log("WARNING", f"중복 실행 방지로 건너뜀: {run_key}", user_id); return
    try:
        skip_reason = get_skip_reason(settings, base_dt)
        if skip_reason and not manual:
            update_run(run_key, "SKIPPED", skip_reason); add_log("INFO", f"예약 실행 건너뜀: {scheduled_hhmm} / {skip_reason}", user_id); return
        lines = build_lines(settings, base_dt)
        if not lines: raise RuntimeError("생성된 게시글이 없습니다. 조 수 또는 템플릿을 확인하세요.")
        add_log("INFO", f"실행 시작: {scheduled_hhmm}, 총 {len(lines)}건", user_id); post_lines(settings, lines, user_id); update_run(run_key, "SUCCESS", f"{len(lines)}건 게시 완료"); add_log("INFO", f"실행 완료: {scheduled_hhmm}, 총 {len(lines)}건", user_id)
    except Exception as exc:
        update_run(run_key, "FAILED", str(exc)); add_log("ERROR", f"실행 실패: {scheduled_hhmm} / {exc}", user_id)

def next_run_estimates(settings: Dict[str, Any]) -> List[Dict[str, str]]:
    now = now_kst(); estimates: List[Dict[str, str]] = []
    if not settings.get("enabled"): return estimates
    for hhmm in settings.get("times", []):
        hour, minute = [int(x) for x in hhmm.split(":")]; candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now: candidate += timedelta(days=1)
        for _ in range(370):
            if not get_skip_reason(settings, candidate): break
            candidate += timedelta(days=1)
        estimates.append({"id": f"expected_{hhmm}", "next_run_time": candidate.strftime("%Y-%m-%d %H:%M:%S")})
    return sorted(estimates, key=lambda item: item["next_run_time"])

def dashboard_status(user_id: Optional[str] = None) -> Dict[str, Any]:
    settings = get_settings(user_id); runtime = get_runtime_values(); heartbeat_row = runtime.get("worker_heartbeat"); heartbeat_value = heartbeat_row["value"] if heartbeat_row else ""; heartbeat_age: Optional[int] = None; worker_alive = False
    if heartbeat_value:
        try:
            heartbeat_dt = datetime.fromisoformat(str(heartbeat_value));
            if heartbeat_dt.tzinfo is None: heartbeat_dt = heartbeat_dt.replace(tzinfo=KST)
            heartbeat_age = int((now_kst() - heartbeat_dt.astimezone(KST)).total_seconds()); worker_alive = heartbeat_age <= 90
        except Exception: heartbeat_age = None
    worker_jobs = runtime.get("worker_jobs", {}).get("value", []) if runtime.get("worker_jobs") else []
    if not isinstance(worker_jobs, list): worker_jobs = []
    if user_id:
        worker_jobs = [job for job in worker_jobs if isinstance(job, dict) and f"_{user_id}_" in str(job.get("id", ""))]
    return {"app": APP_NAME, "now": now_kst().strftime("%Y-%m-%d %H:%M:%S"), "token_ready": api.is_ready(settings), "db": "PostgreSQL" if DATABASE_URL.startswith("postgresql") else "SQLite/local", "enabled": settings["enabled"], "jobs": worker_jobs if worker_jobs else next_run_estimates(settings), "last_run": get_last_run(user_id), "today_skip_reason": get_skip_reason(settings), "worker_heartbeat": heartbeat_value, "worker_heartbeat_age": heartbeat_age, "worker_alive": worker_alive, "worker_state": runtime.get("worker_state", {}).get("value", "대기"), "worker_last_reload": runtime.get("worker_last_reload", {}).get("value", "")}

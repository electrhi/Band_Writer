import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests
from werkzeug.security import check_password_hash


class SupabaseAuthError(RuntimeError):
    pass


def supabase_configured() -> bool:
    return bool(
        os.environ.get("SUPABASE_URL", "").strip()
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )


def _supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise SupabaseAuthError("SUPABASE_URL 환경변수를 설정하세요.")
    return url


def _service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        raise SupabaseAuthError("SUPABASE_SERVICE_ROLE_KEY 환경변수를 설정하세요.")
    return key


def _headers(extra: Dict[str, str] | None = None) -> Dict[str, str]:
    key = _service_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    headers.update(extra or {})
    return headers


def _rest_request(method: str, table: str, *, params: Dict[str, str] | None = None, json_body: Any = None, headers: Dict[str, str] | None = None) -> Any:
    response = requests.request(
        method,
        f"{_supabase_url()}/rest/v1/{table}",
        params=params or {},
        json=json_body,
        headers=_headers(headers),
        timeout=20,
    )
    if response.ok:
        if not response.text:
            return None
        return response.json()
    try:
        detail = response.json()
    except Exception:
        detail = {"message": response.text}
    message = detail.get("message") or detail.get("hint") or detail.get("details") or str(detail)
    raise SupabaseAuthError(f"Supabase REST API 오류: {response.status_code} {message}")


def _password_matches(raw_password: str, stored_password: str, stored_hash: str) -> bool:
    stored_hash = (stored_hash or "").strip()
    stored_password = stored_password or ""
    if stored_hash:
        try:
            if check_password_hash(stored_hash, raw_password):
                return True
        except Exception:
            pass
    return stored_password == raw_password


def sign_in(login_id: str, password: str) -> Dict[str, Any]:
    login_id = login_id.strip()
    if not login_id or not password:
        raise SupabaseAuthError("아이디와 비밀번호를 입력하세요.")
    rows: List[Dict[str, Any]] = _rest_request(
        "GET",
        "work_users",
        params={
            "select": "id,login_id,password,password_hash,display_name,role,is_active,team_no,region_no,worker_type",
            "login_id": f"eq.{login_id}",
            "limit": "1",
        },
    )
    row = rows[0] if rows else None
    if row is None:
        raise SupabaseAuthError("아이디 또는 비밀번호가 맞지 않습니다.")
    if not row["is_active"]:
        raise SupabaseAuthError("비활성화된 계정입니다.")
    if not _password_matches(password, row.get("password", ""), row.get("password_hash", "")):
        raise SupabaseAuthError("아이디 또는 비밀번호가 맞지 않습니다.")
    return {
        "user": {
            "id": str(row["id"]),
            "login_id": row["login_id"],
            "display_name": row["display_name"],
            "role": row["role"],
            "team_no": row["team_no"],
            "region_no": row["region_no"],
            "worker_type": row["worker_type"],
        }
    }


def get_band_access_token(user_id: str) -> str:
    if not user_id:
        return ""
    rows: List[Dict[str, Any]] = _rest_request(
        "GET",
        "band_writer_user_settings",
        params={
            "select": "band_access_token",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )
    row = rows[0] if rows else None
    return str(row.get("band_access_token") or "").strip() if row else ""


def save_band_access_token(user_id: str, access_token: str) -> None:
    if not user_id:
        raise SupabaseAuthError("로그인 정보가 없습니다.")
    _rest_request(
        "POST",
        "band_writer_user_settings",
        params={"on_conflict": "user_id"},
        json_body={
            "user_id": user_id,
            "band_access_token": access_token.strip(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Prefer": "resolution=merge-duplicates"},
    )

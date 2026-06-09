import os
from typing import Any, Dict

import requests


class SupabaseAuthError(RuntimeError):
    pass


def supabase_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL", "").strip() and os.environ.get("SUPABASE_ANON_KEY", "").strip())


def _auth_request(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not base_url or not anon_key:
        raise SupabaseAuthError("SUPABASE_URL과 SUPABASE_ANON_KEY 환경변수를 설정하세요.")
    response = requests.post(
        f"{base_url}{path}",
        json=payload,
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
        timeout=20,
    )
    data: Dict[str, Any]
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text}
    if not response.ok:
        message = data.get("msg") or data.get("message") or data.get("error_description") or "Supabase Auth 요청 실패"
        raise SupabaseAuthError(str(message))
    return data


def sign_in(email: str, password: str) -> Dict[str, Any]:
    data = _auth_request("/auth/v1/token?grant_type=password", {"email": email.strip(), "password": password})
    user = data.get("user") or {}
    if not user.get("id"):
        raise SupabaseAuthError("로그인 응답에서 사용자 정보를 찾을 수 없습니다.")
    return data


def sign_up(email: str, password: str) -> Dict[str, Any]:
    data = _auth_request("/auth/v1/signup", {"email": email.strip(), "password": password})
    user = data.get("user") or {}
    if not user.get("id"):
        raise SupabaseAuthError("회원가입 응답에서 사용자 정보를 찾을 수 없습니다.")
    return data

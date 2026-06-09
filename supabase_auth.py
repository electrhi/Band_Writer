import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Dict

from sqlalchemy import create_engine, text
from werkzeug.security import check_password_hash


class SupabaseAuthError(RuntimeError):
    pass


def supabase_configured() -> bool:
    return bool(os.environ.get("SUPABASE_DATABASE_URL", "").strip())


def _database_url() -> str:
    url = os.environ.get("SUPABASE_DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if not url:
        raise SupabaseAuthError("SUPABASE_DATABASE_URL 환경변수를 설정하세요.")
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return url


def _engine():
    return create_engine(_database_url(), future=True, pool_pre_ping=True)


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
    with _engine().begin() as conn:
        row = conn.execute(
            text(
                """
                select id, login_id, password, password_hash, display_name, role,
                       is_active, team_no, region_no, worker_type
                from public.work_users
                where login_id = :login_id
                limit 1
                """
            ),
            {"login_id": login_id},
        ).mappings().first()
    if row is None:
        raise SupabaseAuthError("아이디 또는 비밀번호가 맞지 않습니다.")
    if not row["is_active"]:
        raise SupabaseAuthError("비활성화된 계정입니다.")
    if not _password_matches(password, row["password"], row["password_hash"] or ""):
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
    with _engine().begin() as conn:
        row = conn.execute(
            text(
                """
                select band_access_token
                from public.band_writer_user_settings
                where user_id = cast(:user_id as uuid)
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
    return str(row["band_access_token"] or "").strip() if row else ""


def save_band_access_token(user_id: str, access_token: str) -> None:
    if not user_id:
        raise SupabaseAuthError("로그인 정보가 없습니다.")
    with _engine().begin() as conn:
        conn.execute(
            text(
                """
                insert into public.band_writer_user_settings (user_id, band_access_token, updated_at)
                values (cast(:user_id as uuid), :access_token, now())
                on conflict (user_id)
                do update set band_access_token = excluded.band_access_token,
                              updated_at = now()
                """
            ),
            {"user_id": user_id, "access_token": access_token.strip()},
        )

import os
import threading
from datetime import timedelta
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, flash, redirect, render_template, request, session, url_for

from band_core import (
    APP_NAME,
    KST,
    api,
    add_log,
    build_lines,
    dashboard_status,
    execute_schedule,
    form_to_settings,
    get_all_enabled_settings,
    get_logs,
    get_settings,
    init_db,
    normalize_settings,
    now_kst,
    save_settings,
)
from supabase_auth import SupabaseAuthError, get_band_access_token, save_band_access_token, sign_in, supabase_configured

scheduler = BackgroundScheduler(timezone=KST)
scheduler_lock = threading.Lock()


def reload_scheduler_jobs() -> None:
    with scheduler_lock:
        if not scheduler.running:
            scheduler.start()
        scheduler.remove_all_jobs()
        enabled_settings = get_all_enabled_settings()
        if not enabled_settings:
            add_log("INFO", "Web 스케줄러 정지 상태 적용")
            return
        applied = []
        for settings in enabled_settings:
            user_id = settings.get("user_id")
            if not settings["band_key"]:
                add_log("ERROR", "Web 스케줄러 시작 실패: 밴드가 선택되어 있지 않습니다.", user_id)
                continue
            for hhmm in settings["times"]:
                hour, minute = [int(x) for x in hhmm.split(":")]
                scheduler.add_job(
                    execute_schedule,
                    CronTrigger(hour=hour, minute=minute, second=0, timezone=KST),
                    args=[hhmm, False, user_id],
                    id=f"band_post_{user_id or 'default'}_{hhmm}",
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=300,
                    coalesce=True,
                )
            applied.append(f"{user_id or 'default'}: {', '.join(settings['times'])}")
        add_log("INFO", f"Web 스케줄러 적용 완료: {' / '.join(applied)}")


def run_due_jobs_from_cron() -> list[str]:
    now = now_kst().replace(second=0, microsecond=0)
    grace_minutes = int(os.environ.get("CRON_GRACE_MINUTES", "5"))
    executed = []
    for settings in get_all_enabled_settings():
        user_id = settings.get("user_id")
        for hhmm in settings["times"]:
            hour, minute = [int(x) for x in hhmm.split(":")]
            candidate = now.replace(hour=hour, minute=minute)
            if candidate > now:
                candidate -= timedelta(days=1)
            diff_seconds = (now - candidate).total_seconds()
            if 0 <= diff_seconds <= grace_minutes * 60:
                execute_schedule(hhmm, False, user_id)
                executed.append(f"{user_id or 'default'}:{hhmm}")
    return executed


def current_user_id() -> str:
    return str(session.get("user_id") or "")


def web_status(user_id: str = "") -> dict:
    status = dashboard_status(user_id or None)
    jobs = []
    if scheduler.running:
        for job in scheduler.get_jobs():
            if user_id and f"_{user_id}_" not in job.id:
                continue
            jobs.append(
                {
                    "id": job.id,
                    "next_run_time": job.next_run_time.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "-",
                }
            )
    if jobs:
        status["jobs"] = jobs
    status["worker_state"] = "Free Web Service 내부 스케줄러 + Supabase Cron Wake"
    status["worker_alive"] = scheduler.running
    return status


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "local-dev-secret-change-me")

    @app.before_request
    def require_login():
        if request.endpoint in {"login", "healthz", "static", "supabase_cron"}:
            return None
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            login_id = request.form.get("login_id", "").strip()
            password = request.form.get("password", "")
            try:
                data = sign_in(login_id, password)
                user = data.get("user") or {}
                session.clear()
                session["user_id"] = user["id"]
                session["user_login_id"] = user.get("login_id") or login_id
                session["user_display_name"] = user.get("display_name") or login_id
                session["user_role"] = user.get("role") or ""
                session["logged_in"] = True
                return redirect(url_for("index"))
            except SupabaseAuthError as exc:
                flash(str(exc), "error")
            except Exception as exc:
                flash(f"Supabase 계정 DB 연결 오류: {exc}", "error")
        return render_template("login.html", app_name=APP_NAME, supabase_configured=supabase_configured())

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        user_id = current_user_id()
        settings = get_settings(user_id)
        try:
            settings["band_access_token"] = get_band_access_token(user_id)
        except Exception as exc:
            settings["band_access_token"] = ""
            add_log("ERROR", f"Supabase BAND Access Token 조회 실패: {exc}", user_id)
        bands: List[dict] = []
        band_error = ""
        if api.is_ready(settings):
            try:
                bands = api.get_bands(api.token_from_settings(settings))
            except Exception as exc:
                band_error = str(exc)
        else:
            band_error = "계정 설정에 BAND Access Token을 저장하세요."
        try:
            preview_lines = build_lines(settings)[:80]
            preview_error = ""
        except Exception as exc:
            preview_lines = []
            preview_error = str(exc)
        return render_template(
            "index.html",
            app_name=APP_NAME,
            settings=settings,
            status=web_status(user_id),
            bands=bands,
            band_error=band_error,
            preview_lines=preview_lines,
            preview_error=preview_error,
            logs=get_logs(user_id=user_id),
            admin_password_enabled=True,
            user_email=session.get("user_login_id", ""),
            user_display_name=session.get("user_display_name", ""),
        )

    @app.route("/settings", methods=["POST"])
    def update_settings_route():
        try:
            user_id = current_user_id()
            current = get_settings(user_id)
            data = form_to_settings(request.form, enabled=current["enabled"])
            save_band_access_token(user_id, data.get("band_access_token", ""))
            data["band_access_token"] = ""
            saved = save_settings(data, user_id)
            reload_scheduler_jobs()
            flash("설정이 저장되었습니다. 실행 중이면 다음 예약부터 새 설정이 반영됩니다.", "success")
            add_log("INFO", f"설정 저장: {saved['band_name'] or '밴드 미선택'} / {', '.join(saved['times'])}", user_id)
        except Exception as exc:
            flash(str(exc), "error")
            add_log("ERROR", f"설정 저장 실패: {exc}", current_user_id() or None)
        return redirect(url_for("index"))

    @app.route("/start", methods=["POST"])
    def start():
        try:
            user_id = current_user_id()
            settings = get_settings(user_id)
            settings["band_access_token"] = get_band_access_token(user_id)
            if not settings["band_key"]:
                raise ValueError("시작 전에 밴드를 선택하고 설정 저장을 먼저 해주세요.")
            if not api.token_from_settings(settings):
                raise ValueError("시작 전에 BAND Access Token을 저장하세요.")
            build_lines(settings)
            settings["enabled"] = True
            settings["band_access_token"] = ""
            save_settings(settings, user_id)
            reload_scheduler_jobs()
            flash("예약 실행을 시작했습니다. Supabase Cron을 연결하면 별도 Ping 없이 예약 시간에 깨워집니다.", "success")
            add_log("INFO", "예약 시작", user_id)
        except Exception as exc:
            flash(str(exc), "error")
            add_log("ERROR", f"예약 시작 실패: {exc}", current_user_id() or None)
        return redirect(url_for("index"))

    @app.route("/stop", methods=["POST"])
    def stop():
        user_id = current_user_id()
        settings = get_settings(user_id)
        settings["enabled"] = False
        save_settings(settings, user_id)
        reload_scheduler_jobs()
        flash("예약 실행을 정지했습니다. 설정값은 그대로 보관됩니다.", "success")
        add_log("INFO", "예약 정지", user_id)
        return redirect(url_for("index"))

    @app.route("/run-now", methods=["POST"])
    def run_now():
        flash("즉시 실행은 비활성화되어 있습니다. 게시글은 설정된 예약 시간에만 작성됩니다.", "error")
        return redirect(url_for("index"))

    @app.route("/api/status")
    def api_status():
        return web_status(current_user_id())

    @app.route("/api/preview", methods=["POST"])
    def api_preview():
        try:
            current = get_settings(current_user_id())
            settings = form_to_settings(request.form, enabled=current["enabled"])
            settings["band_access_token"] = ""
            normalized = normalize_settings(settings)
            lines = build_lines(normalized)[:120]
            return {"ok": True, "lines": lines, "count": len(lines)}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "lines": [], "count": 0}, 400

    @app.route("/api/supabase-cron", methods=["GET", "POST"])
    def supabase_cron():
        expected = os.environ.get("CRON_SECRET", "").strip()
        supplied = request.headers.get("X-Cron-Secret", "") or request.args.get("secret", "")
        if expected and supplied != expected:
            return {"ok": False, "error": "unauthorized"}, 401
        executed = run_due_jobs_from_cron()
        return {"ok": True, "time": now_kst().isoformat(), "executed": executed}

    @app.route("/healthz")
    def healthz():
        return {"ok": True, "time": now_kst().isoformat()}

    return app


init_db()
try:
    reload_scheduler_jobs()
except Exception as exc:
    try:
        add_log("ERROR", f"부팅 중 Web 스케줄러 적용 실패: {exc}")
    except Exception:
        pass
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, use_reloader=False)

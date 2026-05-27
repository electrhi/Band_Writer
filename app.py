import os
import threading
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
    get_logs,
    get_settings,
    init_db,
    normalize_settings,
    now_kst,
    save_settings,
)

scheduler = BackgroundScheduler(timezone=KST)
scheduler_lock = threading.Lock()


def reload_scheduler_jobs() -> None:
    with scheduler_lock:
        if not scheduler.running:
            scheduler.start()
        scheduler.remove_all_jobs()
        settings = get_settings()
        if not settings["enabled"]:
            add_log("INFO", "Web 스케줄러 정지 상태 적용")
            return
        if not settings["band_key"]:
            add_log("ERROR", "Web 스케줄러 시작 실패: 밴드가 선택되어 있지 않습니다.")
            return
        for hhmm in settings["times"]:
            hour, minute = [int(x) for x in hhmm.split(":")]
            scheduler.add_job(
                execute_schedule,
                CronTrigger(hour=hour, minute=minute, timezone=KST),
                args=[hhmm, False],
                id=f"band_post_{hhmm}",
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=300,
                coalesce=True,
            )
        add_log("INFO", f"Web 스케줄러 적용 완료: {', '.join(settings['times'])}")


def run_due_jobs_from_cron() -> list[str]:
    settings = get_settings()
    if not settings["enabled"]:
        return []
    now_hhmm = now_kst().strftime("%H:%M")
    executed = []
    for hhmm in settings["times"]:
        if hhmm == now_hhmm:
            execute_schedule(hhmm, False)
            executed.append(hhmm)
    return executed


def web_status() -> dict:
    status = dashboard_status()
    jobs = []
    if scheduler.running:
        for job in scheduler.get_jobs():
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
        admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
        if request.endpoint in {"login", "healthz", "static", "supabase_cron"}:
            return None
        if admin_password and not session.get("logged_in"):
            return redirect(url_for("login"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
        if not admin_password:
            session["logged_in"] = True
            return redirect(url_for("index"))
        if request.method == "POST":
            if request.form.get("password", "") == admin_password:
                session["logged_in"] = True
                return redirect(url_for("index"))
            flash("비밀번호가 맞지 않습니다.", "error")
        return render_template("login.html", app_name=APP_NAME)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        settings = get_settings()
        bands: List[dict] = []
        band_error = ""
        if api.is_ready:
            try:
                bands = api.get_bands()
            except Exception as exc:
                band_error = str(exc)
        else:
            band_error = "BAND_ACCESS_TOKEN 환경변수가 설정되어 있지 않습니다."
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
            status=web_status(),
            bands=bands,
            band_error=band_error,
            preview_lines=preview_lines,
            preview_error=preview_error,
            logs=get_logs(),
            admin_password_enabled=bool(os.environ.get("ADMIN_PASSWORD", "").strip()),
        )

    @app.route("/settings", methods=["POST"])
    def update_settings_route():
        try:
            current = get_settings()
            data = form_to_settings(request.form, enabled=current["enabled"])
            saved = save_settings(data)
            reload_scheduler_jobs()
            flash("설정이 저장되었습니다. 실행 중이면 다음 예약부터 새 설정이 반영됩니다.", "success")
            add_log("INFO", f"설정 저장: {saved['band_name'] or '밴드 미선택'} / {', '.join(saved['times'])}")
        except Exception as exc:
            flash(str(exc), "error")
            add_log("ERROR", f"설정 저장 실패: {exc}")
        return redirect(url_for("index"))

    @app.route("/start", methods=["POST"])
    def start():
        try:
            settings = get_settings()
            if not settings["band_key"]:
                raise ValueError("시작 전에 밴드를 선택하고 설정 저장을 먼저 해주세요.")
            build_lines(settings)
            settings["enabled"] = True
            save_settings(settings)
            reload_scheduler_jobs()
            flash("예약 실행을 시작했습니다. Supabase Cron을 연결하면 별도 Ping 없이 예약 시간에 깨워집니다.", "success")
            add_log("INFO", "예약 시작")
        except Exception as exc:
            flash(str(exc), "error")
            add_log("ERROR", f"예약 시작 실패: {exc}")
        return redirect(url_for("index"))

    @app.route("/stop", methods=["POST"])
    def stop():
        settings = get_settings()
        settings["enabled"] = False
        save_settings(settings)
        reload_scheduler_jobs()
        flash("예약 실행을 정지했습니다. 설정값은 그대로 보관됩니다.", "success")
        add_log("INFO", "예약 정지")
        return redirect(url_for("index"))

    @app.route("/run-now", methods=["POST"])
    def run_now():
        threading.Thread(target=execute_schedule, args=(now_kst().strftime("%H:%M:%S"), True), daemon=True).start()
        flash("즉시 실행을 시작했습니다. 진행 상황은 로그에서 확인하세요.", "success")
        return redirect(url_for("index"))

    @app.route("/api/status")
    def api_status():
        return web_status()

    @app.route("/api/preview", methods=["POST"])
    def api_preview():
        try:
            current = get_settings()
            settings = form_to_settings(request.form, enabled=current["enabled"])
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

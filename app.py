import os
import threading
from typing import List

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from band_core import (
    APP_NAME,
    api,
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
    add_log,
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "local-dev-secret-change-me")

    @app.before_request
    def require_login():
        admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
        if request.endpoint in {"login", "healthz", "static"}:
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
            status=dashboard_status(),
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
            flash("설정이 저장되었습니다. Worker가 저장된 값을 읽어 다음 예약부터 반영합니다.", "success")
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
            flash("예약 실행을 시작 상태로 변경했습니다. 실제 실행은 Background Worker가 담당합니다.", "success")
            add_log("INFO", "예약 시작 상태 저장")
        except Exception as exc:
            flash(str(exc), "error")
            add_log("ERROR", f"예약 시작 실패: {exc}")
        return redirect(url_for("index"))

    @app.route("/stop", methods=["POST"])
    def stop():
        settings = get_settings()
        settings["enabled"] = False
        save_settings(settings)
        flash("예약 실행을 정지했습니다. 설정값은 그대로 보관됩니다.", "success")
        add_log("INFO", "예약 정지 상태 저장")
        return redirect(url_for("index"))

    @app.route("/run-now", methods=["POST"])
    def run_now():
        threading.Thread(target=execute_schedule, args=(now_kst().strftime("%H:%M:%S"), True), daemon=True).start()
        flash("즉시 실행을 시작했습니다. 진행 상황은 로그에서 확인하세요.", "success")
        return redirect(url_for("index"))

    @app.route("/api/status")
    def api_status():
        return jsonify(dashboard_status())

    @app.route("/api/preview", methods=["POST"])
    def api_preview():
        try:
            current = get_settings()
            settings = form_to_settings(request.form, enabled=current["enabled"])
            normalized = normalize_settings(settings)
            lines = build_lines(normalized)[:120]
            return jsonify({"ok": True, "lines": lines, "count": len(lines)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "lines": [], "count": 0}), 400

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True, "time": now_kst().isoformat()})

    return app


init_db()
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, use_reloader=False)

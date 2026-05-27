import signal
import threading
import time
from typing import Dict, List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from band_core import (
    KST,
    add_log,
    api,
    dashboard_status,
    execute_schedule,
    get_settings,
    init_db,
    now_kst,
    set_runtime,
)

scheduler = BackgroundScheduler(timezone=KST)
scheduler_lock = threading.Lock()
stop_event = threading.Event()
last_signature = ""


def settings_signature(settings: Dict) -> str:
    return repr(
        {
            "enabled": settings.get("enabled"),
            "times": settings.get("times"),
            "band_key": settings.get("band_key"),
            "dcu_count": settings.get("dcu_count"),
            "modem_counts": settings.get("modem_counts"),
            "date_format": settings.get("date_format"),
            "modem_template": settings.get("modem_template"),
            "dcu_template": settings.get("dcu_template"),
            "tail_templates": settings.get("tail_templates"),
            "orders": [settings.get("modem_zone_order"), settings.get("modem_team_order"), settings.get("dcu_team_order")],
            "post_interval_seconds": settings.get("post_interval_seconds"),
            "retry_interval_seconds": settings.get("retry_interval_seconds"),
            "retry_limit": settings.get("retry_limit"),
            "do_push": settings.get("do_push"),
        }
    )


def publish_jobs_status() -> None:
    jobs: List[Dict[str, str]] = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "next_run_time": job.next_run_time.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "-",
            }
        )
    set_runtime("worker_jobs", jobs)
    set_runtime("worker_state", "실행 중" if jobs else "대기")


def reload_jobs_if_needed(force: bool = False) -> None:
    global last_signature
    settings = get_settings()
    signature = settings_signature(settings)
    if not force and signature == last_signature:
        return
    with scheduler_lock:
        scheduler.remove_all_jobs()
        if settings["enabled"]:
            if not settings["band_key"]:
                add_log("ERROR", "Worker 예약 시작 실패: 밴드가 선택되어 있지 않습니다.")
            else:
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
                add_log("INFO", f"Worker 예약 적용: {', '.join(settings['times'])}")
        else:
            add_log("INFO", "Worker 예약 정지 상태 확인")
        last_signature = signature
        set_runtime("worker_last_reload", now_kst().isoformat())
        publish_jobs_status()


def heartbeat_loop() -> None:
    while not stop_event.is_set():
        try:
            set_runtime("worker_heartbeat", now_kst().isoformat())
            publish_jobs_status()
        except Exception as exc:
            print(f"heartbeat error: {exc}", flush=True)
        stop_event.wait(20)


def settings_watch_loop() -> None:
    while not stop_event.is_set():
        try:
            reload_jobs_if_needed()
        except Exception as exc:
            add_log("ERROR", f"Worker 설정 감시 오류: {exc}")
        stop_event.wait(10)


def handle_stop(signum, frame) -> None:
    stop_event.set()


def main() -> None:
    init_db()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    set_runtime("worker_state", "부팅 중")
    add_log("INFO", f"Worker 부팅: token={'OK' if api.is_ready else 'MISSING'}")
    scheduler.start()
    reload_jobs_if_needed(force=True)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=settings_watch_loop, daemon=True).start()
    while not stop_event.is_set():
        stop_event.wait(1)
    set_runtime("worker_state", "종료")
    add_log("WARNING", "Worker 종료")
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()

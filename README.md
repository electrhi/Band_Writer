# Band Auto Writer - Render 배포형

기존 Tkinter 프로그램을 Render에서 실행 가능한 Flask 웹앱으로 변환한 버전입니다.

## 주요 기능

- BAND 자동 글쓰기 스케줄 실행
- GitHub → Render 자동 배포 가능
- 웹 UI에서 밴드, 시간, 조 수, 글 형식 설정
- 설정 저장 시 기존 설정 덮어쓰기
- 현재 설정 상태, 다음 실행 시간, 최근 실행 결과, 로그 표시
- 글 형식 템플릿 자유 변경
- 중복 실행 방지용 실행 이력 저장
- BAND 토큰은 코드에 저장하지 않고 환경변수로 관리

## 글 형식 템플릿 변수

- `{date}`: 날짜 형식에 따라 생성된 날짜
- `{zone}`: 권역 번호
- `{team}`: 조 번호
- `{type}`: 모뎀 / DCU / TBM 등 구분용

예시:

```text
{date} {zone}권역 모뎀{team}조
{date} DCU {team}조
{date} TBM
```

## 로컬 실행

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
copy .env.example .env
# .env 파일에 BAND_ACCESS_TOKEN, ADMIN_PASSWORD 입력
python app.py
```

브라우저에서 `http://localhost:5000` 접속

## Render 배포 방법

1. Render Dashboard → New → Web Service 선택
2. GitHub 저장소 `electrhi/Band_Writer` 연결
3. 아래 값을 입력합니다.

| 항목 | 값 |
|---|---|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --workers 1 --threads 4 --timeout 180` |
| Environment | Python |

4. Environment Variables에 아래 값을 추가합니다.

| Key | 설명 |
|---|---|
| `BAND_ACCESS_TOKEN` | BAND 개발자센터에서 새로 발급한 토큰 |
| `ADMIN_PASSWORD` | 웹 관리자 화면 접속 비밀번호 |
| `SECRET_KEY` | 임의의 긴 랜덤 문자열 |
| `TZ` | `Asia/Seoul` |
| `DATABASE_URL` | PostgreSQL 연결 문자열. render.yaml 사용 시 자동 연결 가능 |

## 중요 운영 메모

- Render 무료 Web Service는 유휴 상태에서 잠들 수 있으므로 정확한 시간 실행이 필요하면 유료 인스턴스 또는 Background Worker 구조를 권장합니다.
- `gunicorn --workers 1`을 유지하세요. worker 수를 늘리면 같은 예약 작업이 중복 실행될 수 있습니다.
- BAND_ACCESS_TOKEN은 절대 GitHub에 올리지 마세요. 이미 노출된 토큰은 BAND 개발자센터에서 폐기/재발급하는 것을 권장합니다.

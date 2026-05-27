# Band Auto Writer - Render Web + Worker 구조

기존 Tkinter 프로그램을 Render에서 실행 가능한 Flask 웹앱 + Background Worker 구조로 변환한 버전입니다.

## 구조

```text
Free Web Service
- 모바일 설정 화면
- 설정 저장
- 글쓰기 미리보기
- 시작/정지 상태 저장

Background Worker
- DB에 저장된 설정 읽기
- 정해진 시간에 BAND 글쓰기 실행
- 실행 로그 및 Worker 상태 저장
```

## 주요 기능

- BAND 자동 글쓰기 스케줄 실행
- GitHub → Render 자동 배포 가능
- 웹 UI에서 밴드, 시간, 조 수, 글 형식 설정
- 설정 저장과 시작/정지 버튼 분리
- 모바일 하단 고정 버튼 지원
- 현재 설정 상태, Worker 상태, 다음 실행 시간, 최근 실행 결과, 로그 표시
- 글 형식 템플릿 자유 변경
- 중복 실행 방지용 실행 이력 저장
- BAND 토큰은 코드에 저장하지 않고 환경변수로 관리

## 파일 구성

```text
app.py              # Flask 웹 설정 화면
worker.py           # Background Worker 예약 실행
band_core.py        # DB, BAND API, 설정, 글 생성 공통 로직
requirements.txt
render.yaml         # Web Service + Worker + Postgres Blueprint
.env.example
templates/
  index.html
  login.html
```

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

## Render 배포 방법

### 1. Blueprint로 생성하는 방법

1. Render Dashboard → New → Blueprint
2. GitHub 저장소 `electrhi/Band_Writer` 선택
3. `render.yaml`을 인식하면 아래 3개 리소스가 생성됩니다.
   - `band-auto-writer-web`
   - `band-auto-writer-worker`
   - `band-auto-writer-db`
4. 환경변수에 아래 값을 넣습니다.

| Key | 대상 | 설명 |
|---|---|---|
| `BAND_ACCESS_TOKEN` | Web, Worker | BAND 개발자센터에서 새로 발급한 토큰 |
| `ADMIN_PASSWORD` | Web | 웹 관리자 화면 접속 비밀번호 |
| `SECRET_KEY` | Web | 임의의 긴 랜덤 문자열 |
| `TZ` | Web, Worker | `Asia/Seoul` |
| `DATABASE_URL` | Web, Worker | 같은 DB 연결 문자열 |

### 2. 수동으로 생성하는 방법

#### Web Service

| 항목 | 값 |
|---|---|
| Type | Web Service |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --workers 1 --threads 4 --timeout 180` |

#### Background Worker

| 항목 | 값 |
|---|---|
| Type | Background Worker |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python worker.py` |

Web Service와 Background Worker의 `DATABASE_URL`은 반드시 같아야 합니다.

## 사용 방법

1. 웹 주소 접속
2. 관리자 비밀번호 입력
3. 밴드 선택
4. 시간, 조 수, 글 형식 입력
5. 미리보기 확인
6. `설정 저장`
7. `시작`
8. 상단 상태에서 Worker가 정상인지 확인

## 중요 운영 메모

- Free Web Service는 유휴 상태에서 잠들 수 있으므로 예약 실행을 맡기면 안 됩니다.
- 예약 실행은 `worker.py`가 담당합니다.
- Render Free Postgres는 30일 제한이 있으므로 장기 운영은 Supabase/Neon 같은 외부 무료 PostgreSQL을 권장합니다.
- `BAND_ACCESS_TOKEN`은 절대 GitHub에 올리지 마세요. 이미 노출된 토큰은 BAND 개발자센터에서 폐기/재발급하는 것을 권장합니다.

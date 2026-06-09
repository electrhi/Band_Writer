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
- 로그인은 Supabase `public.work_users` 테이블의 아이디/비밀번호로 관리
- BAND 토큰은 계정별 설정값으로 저장
- 조별 인원 이름을 저장하고 `{members}` 템플릿 변수로 게시글에 표시

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
- `{members}`: 해당 조에 편성된 인원 이름 (`김철수/김영희` 형식)

예시:

```text
{date} 모뎀 {team}조 {members}
{date} DCU {team}조 {members}
{date} TBM
```

## Render 배포 방법

### 1. Blueprint로 생성하는 방법

1. Render Dashboard → New → Blueprint
2. GitHub 저장소 `electrhi/Band_Writer` 선택
3. `render.yaml`을 인식하면 아래 2개 리소스가 생성됩니다.
   - `band-auto-writer-web`
   - `band-auto-writer-worker`
4. Supabase 프로젝트의 `public.work_users` 테이블 계정을 준비하고, Render 환경변수에 아래 값을 넣습니다.

| Key | 대상 | 설명 |
|---|---|---|
| `SUPABASE_URL` | Web | `https://blbmdnygvoqyrovvlrrh.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Web | `public.work_users`와 BAND 토큰 설정 조회/저장용 Supabase service role 또는 secret key |
| `SECRET_KEY` | Web | 임의의 긴 랜덤 문자열 |
| `TZ` | Web, Worker | `Asia/Seoul` |
| `DATABASE_URL` | Web, Worker | Render PostgreSQL `band-auto-writer-db` 연결 문자열 |

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

Web Service와 Background Worker의 `DATABASE_URL`은 반드시 같은 Render PostgreSQL 연결 문자열이어야 합니다. Blueprint 배포 시 `band-auto-writer-db`에서 자동 연결됩니다.

## 사용 방법

1. 웹 주소 접속
2. Supabase `work_users` 테이블의 아이디/비밀번호로 로그인
3. BAND Access Token 저장
4. 밴드 선택
5. 시간, 조 수, 조원 이름, 글 형식 입력
6. 미리보기 확인
7. `설정 저장`
8. `시작`
9. 상단 상태에서 Worker가 정상인지 확인

## 중요 운영 메모

- Free Web Service는 유휴 상태에서 잠들 수 있으므로 예약 실행을 맡기면 안 됩니다.
- 예약 실행은 `worker.py`가 담당합니다.
- 계정 관리는 Supabase `public.work_users` 테이블이 담당하고, 계정별 BAND 토큰은 Supabase `public.band_writer_user_settings` 테이블에 `work_users.id`로 연결되어 저장됩니다. 예약 시간, 조편성, 템플릿, 로그는 Render PostgreSQL에 저장됩니다.
- `BAND_ACCESS_TOKEN`은 절대 GitHub에 올리지 마세요. 현재 버전은 웹 화면에서 계정별 BAND Access Token을 저장합니다. 이미 노출된 토큰은 BAND 개발자센터에서 폐기/재발급하는 것을 권장합니다.

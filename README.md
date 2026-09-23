# 코레일 자동 예약 텔레그램 봇

[korail-mobile-api](https://github.com/yakisoba0728/korail-mobile-api)(코레일톡 앱 API 클라이언트)로 만든
텔레그램 봇입니다. 열차를 고르면 빈 좌석이 날 때까지 계속 조회하다가, 좌석이 보이면 곧바로
**결제 전 예약**을 잡고 텔레그램으로 알려 줍니다. 결제는 코레일톡 앱에서 기한 안에 하면 됩니다.

KORAIL·SR 통합(2026-09-01) 이후라서 기본값으로 SRT 열차도 함께 조회합니다(`INCLUDE_SRT`).

## 사용법

| 명령 | 설명 |
|---|---|
| `/search` 또는 **🔎 예약하기** | 출발역 → 도착역 → 날짜 → 시간 → 열차 체크 → **▶️ 예약 시작** |
| `/status` 또는 **📡 진행 중** | 진행 중인 자동 예약 목록 (개별 중지 버튼 포함) |
| `/stop` | 자동 예약 모두 중지 |
| `/reservations` 또는 **🎫 예약 내역** | 코레일 계정의 예약 내역 |
| `/cancel` | 입력 중인 조회 취소 |

- 열차 목록에서 여러 편을 체크할 수 있고, 출발 시각이 빠른 열차부터 예약을 시도합니다.
- **🔁 환승 보기** 버튼으로 환승 여정(2구간)을 볼 수 있고, 직통 열차가 없는 구간은 자동으로 환승 여정을 보여 줍니다.
  환승은 **두 구간 모두** 원하는 등급에 좌석이 있을 때 한 번에(PNR 하나로) 예약합니다.
- **💺 좌석** 버튼으로 일반실 / 특실 / 일반실 우선·특실을 바꿀 수 있습니다.
- **👤 인원** 버튼으로 어른 1~4명을 고릅니다.
- 예약에 성공하면 **🗑 이 예약 취소** 버튼이 붙은 알림이 옵니다.
- 체크한 열차가 모두 출발했거나, `MACRO_MAX_HOURS` 가 지나면 자동으로 멈춥니다.
- 예약 요청 도중 통신이 끊기면, 중복 예약을 막기 위해 재시도하지 않고 멈춘 뒤 확인을 요청합니다.

## 서버 배포 (Oracle Cloud Ubuntu 22.04 + Docker + Caddy)

Caddy 가 `korailbotkorailbot.duckdns.org` → `localhost:8000` 으로 프록시하고 있다는 전제입니다.
봇 컨테이너는 `127.0.0.1:8000` 에만 열립니다.

```bash
ssh ubuntu@168.107.11.77

git clone https://github.com/hietzsche87/SRT.git korail-bot
cd korail-bot

cp .env.example .env
nano .env        # TELEGRAM_BOT_TOKEN, KORAIL_MEMBER_NO, KORAIL_PASSWORD, WEBHOOK_SECRET 입력

docker compose up -d --build
docker compose logs -f
```

1. `WEBHOOK_SECRET` 은 `openssl rand -hex 32` 로 만들면 됩니다.
2. 처음에는 `ALLOWED_USER_IDS` 를 비워 두고 봇에게 `/start` 를 보내면 내 텔레그램 ID 를 알려 줍니다.
   그 값을 `.env` 에 넣고 `docker compose up -d` 로 다시 띄우세요.
3. 봇이 시작할 때 텔레그램에 웹훅(`https://korailbotkorailbot.duckdns.org/telegram`)을 스스로 등록합니다.

Caddyfile 은 지금처럼 이 정도면 충분합니다.

```caddy
korailbotkorailbot.duckdns.org {
    reverse_proxy localhost:8000
}
```

### 업데이트

```bash
cd ~/korail-bot && git pull && docker compose up -d --build
```

korail-mobile-api 는 빌드할 때마다 upstream `main` 의 **최신 커밋**을 받습니다. 라이브러리만 새로 나왔을 때도
`docker compose up -d --build` 한 번이면 반영됩니다. 들어간 버전은
`docker compose exec korail-bot cat /opt/korail-mobile-api/VERSION` 로 확인할 수 있습니다.
특정 커밋에 묶고 싶으면 `docker compose build --build-arg KORAIL_API_REF=<커밋>` 으로 빌드하세요.

## 설정 (.env)

전체 목록과 설명은 [`.env.example`](.env.example) 을 보세요. 주요 항목:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | (필수) | BotFather 토큰 |
| `ALLOWED_USER_IDS` | 비어 있음 | 봇을 쓸 수 있는 텔레그램 ID (쉼표 구분) |
| `KORAIL_MEMBER_NO` / `KORAIL_PASSWORD` | (필수) | 코레일 회원번호·휴대폰·이메일 / 비밀번호 |
| `WEBHOOK_BASE_URL` | 비어 있음 | 비우면 롱폴링 |
| `MACRO_INTERVAL_SECONDS` / `MACRO_JITTER_SECONDS` | 3 / 2 | 조회 간격 = 3초 + 0~2초 |
| `INCLUDE_SRT` | true | SRT 열차 함께 조회 |

## 개발

```bash
git clone https://github.com/yakisoba0728/korail-mobile-api.git ../korail-mobile-api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=../korail-mobile-api/src:.
pytest
```

## 주의

- 코레일 비공개 앱 API 를 쓰는 비공식 도구입니다. 조회 간격을 너무 짧게 잡으면 계정·IP 가 차단될 수 있습니다.
- 봇이 잡는 것은 **결제 전 예약**입니다. 결제 기한이 지나면 자동으로 취소됩니다.
- 2단계 인증이 필요한 계정은 로그인이 안 될 수 있습니다. 코레일톡 앱에서 인증을 먼저 마치세요.

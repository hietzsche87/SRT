# 작업 규칙

## 사용자에게 질문하고 답을 기다릴 때 (텔레그램 대기 알림)

준비:
- 이 프로젝트에 `tools/telegram_ask.py` 가 없으면 `C:\test\tools\telegram_ask.py` 와 `C:\test\tools\telegram_notify.py` 를
  `tools/` 로 복사한다. `C:\test` 가 없는 컴퓨터(클라우드 세션 포함)면 GitHub `hietzsche87/subtitle-ocr-toolkit` 의
  `tools/` 에서 두 파일을 받는다. `telegram_ask.py` 가 `telegram_notify.py` 의 `BOT_TOKEN`, `CHAT_ID`, `send()` 를 쓰므로
  두 파일은 항상 같이 둔다.
- 두 파일은 `.gitignore` 에 있다. `telegram_notify.py` 에 봇 토큰이 들어 있으므로 **절대 커밋하지 않는다** (이 저장소는 공개).

절차:
1. 채팅창에 질문을 쓴 직후, 아래 명령을 백그라운드(`run_in_background: true`)로 실행한다.
   `python tools/telegram_ask.py "질문 한 줄 요약"`
   - 텔레그램으로 "Claude가 답을 기다리는 중 + 질문 요약"을 한 번 보내고, 그 뒤 5초마다 "⏳ 질문 대기 중" 알림을 보낸다.
   - 사용자가 텔레그램에 아무 메시지나 보내면 알림을 멈추고 `STOPPED_BY_USER` 를 출력하고 끝난다.
   - 6시간 동안 메시지가 없으면 `TIMEOUT` 으로 끝난다.
2. 답은 텔레그램이 아니라 채팅창으로 온다. 사용자가 채팅창에서 먼저 답하면 그 백그라운드 작업을 TaskStop 으로 멈춘다.
3. 기다리는 동안 질문과 상관없는 작업은 계속한다.
4. getUpdates 는 한 번에 하나만 받을 수 있으니 이 스크립트를 동시에 두 개 돌리지 않는다. 질문이 여러 개면 한 번에 묶어서 묻는다.
5. Windows 에서 `python` 이 Python Install Manager 창을 띄우면
   `C:\Users\KANG\AppData\Local\Programs\Python\Python312\python.exe` 전체 경로로 실행한다.

## 서버 명령 안내

- 명령을 알려 줄 때는 항상 **PowerShell 창을 새로 연 상태**를 가정한다: `ssh -t ubuntu@168.107.11.77 "..."` 한 줄로 준다.
- 사용자가 이미 서버 안(`ubuntu@korail-bot:~$`)에 있을 수 있으므로, 서버 안에서 쓸 명령(ssh 없이 따옴표 안 명령)도 같이 적는다.

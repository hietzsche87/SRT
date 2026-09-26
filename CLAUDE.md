# 작업 규칙

## 서버 명령 안내

- 명령을 알려 줄 때는 항상 **PowerShell 창을 새로 연 상태**를 가정한다: `ssh -t korail "..."` 한 줄로 준다.
  - `korail` 은 사용자 PC 의 `~/.ssh/config` 별명이다 (HostName 168.107.11.77, User ubuntu,
    IdentityFile ~/.ssh/korail.key — 원본은 `C:\Users\KANG\Downloads\ssh-key-2026-09-21 (1).key`).
    **올바른 키는 이름에 `(1)` 이 붙은 파일이다.** `(1)` 없는 `ssh-key-2026-09-21.key` 는 이 서버 키가 아니다.
  - 별명 설정이 안 된 PC 라면 `ssh -i "C:\Users\KANG\Downloads\ssh-key-2026-09-21 (1).key" -t ubuntu@168.107.11.77 "..."` 를 쓴다.
- 사용자가 이미 서버 안(`ubuntu@korail-bot:~$`)에 있을 수 있으므로, 서버 안에서 쓸 명령(ssh 없이 따옴표 안 명령)도 같이 적는다.
- 별명 설정 명령 (PowerShell, 한 번만):
  ```powershell
  New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null
  Copy-Item "$HOME\Downloads\ssh-key-2026-09-21 (1).key" "$HOME\.ssh\korail.key" -Force
  icacls "$HOME\.ssh\korail.key" /inheritance:r /grant:r "${env:USERNAME}:R" | Out-Null
  Add-Content "$HOME\.ssh\config" "`nHost korail`n    HostName 168.107.11.77`n    User ubuntu`n    IdentityFile ~/.ssh/korail.key`n"
  ssh korail "echo 접속 성공"
  ```

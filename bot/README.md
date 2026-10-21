# 훈련일지 Telegram 봇

```
Telegram 메시지 ─▶ Vercel 함수(bot/api/telegram.js) ─▶ GitHub repository_dispatch
   ─▶ telegram-post.yml: Claude 로 마크다운 변환 → 브랜치/PR 생성 → Telegram 으로 PR 링크
   ─▶ (검수 후 Merge) deploy.yml: 빌드·배포 → 운영 페이지 확인 → Telegram 으로 결과
```

## 사용법

| 하고 싶은 것 | 방법 |
| --- | --- |
| 새 글 | 훈련일지 텍스트를 그대로 전송 (사진 1장 + 캡션으로 보내면 그 사진이 썸네일) |
| 사진 추가 | 봇이 보낸 `PR #번호` 메시지에 **답장**으로 사진 전송. 캡션에 `썸네일` → 썸네일 지정 |
| 게시 | PR 링크에서 내용 확인 후 Merge → 배포 완료 시 운영 페이지 링크 알림 |

- 썸네일이 없는 상태에서 첫 사진을 추가하면 자동으로 썸네일이 되고, 이후 사진은 본문 맨 아래에 붙습니다.
  위치를 바꾸고 싶으면 PR 에서 직접 수정하세요.
- 원본 화질로 올리려면 사진을 '파일'로 보내세요.

## 최초 설정

### 1. Telegram 봇 만들기
1. Telegram 에서 `@BotFather` → `/newbot` → 봇 토큰 발급
2. 봇에게 아무 메시지나 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 에서 `chat.id` 확인
   (그룹방에서 쓰려면 봇을 그룹에 초대하고 BotFather `/setprivacy` → Disable)

### 2. GitHub 설정
- **Settings → Secrets and variables → Actions** 에 추가
  - `ANTHROPIC_API_KEY`
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID` (배포 결과를 받을 chat id)
- **Settings → Actions → General → Workflow permissions** 에서
  "Allow GitHub Actions to create and approve pull requests" 체크
- Vercel 에서 쓸 **fine-grained PAT** 발급: 이 저장소만, `Contents: Read and write` 권한

### 3. Vercel 배포
1. Vercel 에서 이 저장소로 프로젝트 생성, **Root Directory 를 `bot`** 으로 지정 (Framework: Other)
2. Environment Variables
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_WEBHOOK_SECRET` (임의의 긴 문자열, 영문/숫자/`_`/`-`)
   - `ALLOWED_CHAT_IDS` (쉼표 구분, 허용되지 않은 채팅은 무시)
   - `GITHUB_TOKEN` (위 PAT)
   - `GITHUB_REPO` = `HyunJay52/aquacrew.github.io`
3. 배포 후 웹훅 등록

```bash
curl "https://api.telegram.org/bot<토큰>/setWebhook" \
  -d "url=https://<vercel-도메인>/api/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>" \
  -d 'allowed_updates=["message"]'
```

봇에게 `/help` 를 보내 응답이 오면 연결 완료입니다.

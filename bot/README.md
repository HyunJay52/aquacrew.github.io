# 훈련일지 Telegram 봇

```
Telegram 메시지 ─▶ Vercel 함수(bot/api/telegram.js) ─▶ GitHub repository_dispatch
   ─▶ telegram-post.yml
        새 글 (handle.py): Claude 로 마크다운 변환 → 브랜치/PR 생성 → Telegram 으로 PR 링크
        수정 (edit.py)   : 답장 대상 PR/게시 글에 텍스트·사진 수정 커밋 → Telegram 으로 결과
   ─▶ (검수 후 Merge) deploy.yml: 빌드·배포 → 운영 페이지 확인 → Telegram 으로 결과
```

## 사용법

### 새 글
훈련일지 텍스트를 그대로 전송 (사진 1장 + 캡션으로 보내면 그 사진이 썸네일)

### 수정 — 봇 메시지에 **답장**
`PR #번호` 메시지, 배포 완료 알림(`[post:폴더]`), `/글` 검색 결과 어디에 답장해도 됩니다.

| 하고 싶은 것 | 답장 내용 |
| --- | --- |
| 날짜·제목·내용 수정 | 자연어로 요청. 예) `날짜를 10월 19일로`, `메인셋 인터벌 1'40"로` |
| 사진 추가 | 사진 전송 (썸네일이 없으면 썸네일, 있으면 본문 맨 아래) |
| 썸네일 변경 | 사진 + 캡션 `썸네일` 또는 `/썸네일 2` (기존 2번 사진) |
| 사진 목록 | `/사진` |
| 사진 삭제 | `/삭제 2 3` |
| 사진 교체 | 사진 + 캡션 `/교체 2` |
| 예전 글 찾기 | (답장 없이) `/글 2026-10-18` 또는 `/글 송년회` |

- **검수 중인 글**(열린 PR)은 그 PR 에 바로 커밋됩니다.
- **게시된 글**은 `edit/<폴더>` 브랜치에 수정 PR 이 열리고, 이어지는 수정은 같은 PR 에 쌓입니다. 검수 후 Merge 하면 배포·알림됩니다.
- 게시된 글의 제목을 바꾸면 주소(`/p/제목/`)가 바뀌므로 이전 주소를 `aliases` 로 자동 연결합니다.
- 썸네일을 바꾸거나 사진을 교체하면, 더 이상 쓰이지 않는 이전 파일은 삭제됩니다.
- 원본 화질로 올리려면 사진을 '파일'로 보내세요. (jpg, png, webp, gif)

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

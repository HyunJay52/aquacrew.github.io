// Telegram 웹훅 → GitHub repository_dispatch 중계 (Vercel Serverless Function)
//
// 환경변수
//   TELEGRAM_BOT_TOKEN       봇 토큰 (BotFather)
//   TELEGRAM_WEBHOOK_SECRET  setWebhook 의 secret_token 과 동일한 값
//   ALLOWED_CHAT_IDS         메시지를 받을 chat id 목록 (쉼표 구분)
//   GITHUB_TOKEN             repo 의 Contents: read & write 권한이 있는 fine-grained PAT
//   GITHUB_REPO              예: HyunJay52/aquacrew.github.io

const HELP = [
    "🏊 AquaCrew 훈련일지 봇",
    "",
    "• 새 글: 훈련일지 텍스트를 그대로 보내주세요. (사진 1장 + 캡션으로 보내면 그 사진이 썸네일)",
    "• 사진 추가: 봇이 보낸 'PR #번호' 메시지에 답장으로 사진을 보내주세요.",
    "  캡션에 '썸네일' 이라고 쓰면 썸네일로 지정됩니다.",
    "• PR 을 검수 후 GitHub 에서 Merge 하면 배포되고, 배포 결과를 다시 알려드려요.",
].join("\n");

async function telegram(method, body) {
    const res = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) console.error(`telegram ${method} failed`, res.status, await res.text());
}

function reply(msg, text) {
    return telegram("sendMessage", {
        chat_id: msg.chat.id,
        text,
        reply_parameters: { message_id: msg.message_id },
    });
}

async function dispatch(eventType, payload) {
    const res = await fetch(`https://api.github.com/repos/${process.env.GITHUB_REPO}/dispatches`, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
            Accept: "application/vnd.github+json",
            "User-Agent": "aquacrew-telegram-bot",
        },
        body: JSON.stringify({ event_type: eventType, client_payload: payload }),
    });
    if (!res.ok) throw new Error(`GitHub dispatch ${res.status}: ${await res.text()}`);
}

// 사진(압축 전송) 또는 이미지 파일(원본 전송)에서 file_id 와 확장자를 꺼낸다
function imageOf(msg) {
    if (msg.photo?.length) return { file_id: msg.photo[msg.photo.length - 1].file_id, ext: ".jpg" };
    const doc = msg.document;
    if (doc?.mime_type?.startsWith("image/")) {
        const ext = (doc.file_name?.match(/\.[A-Za-z0-9]+$/)?.[0] ?? ".jpg").toLowerCase();
        return { file_id: doc.file_id, ext };
    }
    return null;
}

async function handle(msg) {
    const allowed = (process.env.ALLOWED_CHAT_IDS ?? "").split(",").map((s) => s.trim());
    if (!allowed.includes(String(msg.chat.id))) {
        console.warn("ignored chat", msg.chat.id);
        return;
    }

    const text = (msg.text ?? msg.caption ?? "").trim();
    const image = imageOf(msg);
    const replied = msg.reply_to_message;
    const pr = (replied?.text ?? replied?.caption ?? "").match(/PR #(\d+)/)?.[1];

    if (text.startsWith("/start") || text.startsWith("/help")) {
        return reply(msg, HELP);
    }

    const base = { chat_id: msg.chat.id, message_id: msg.message_id };

    if (image && pr) {
        await dispatch("telegram-image", {
            ...base,
            pr: Number(pr),
            file_id: image.file_id,
            ext: image.ext,
            thumbnail: text.includes("썸네일"),
        });
        return;
    }

    if (image && !text) {
        return reply(msg, "사진은 봇이 보낸 'PR #번호' 메시지에 답장으로 보내주세요.");
    }

    if (text.length < 20) {
        return reply(msg, "훈련일지 내용이 너무 짧아요. /help 로 사용법을 확인해주세요.");
    }

    await dispatch("telegram-post", { ...base, text, file_id: image?.file_id ?? null, ext: image?.ext ?? null });
    return reply(msg, "📥 접수했어요. 마크다운 변환 후 PR 을 만들고 있어요… (1~2분)");
}

export default async function handler(req, res) {
    if (req.method !== "POST") return res.status(405).end();
    // 환경변수가 비어 있으면 헤더 없는 요청(undefined === undefined)이 통과하므로 함께 막는다
    const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
    if (!secret || req.headers["x-telegram-bot-api-secret-token"] !== secret) {
        if (!secret) console.error("TELEGRAM_WEBHOOK_SECRET is not set");
        return res.status(401).end();
    }

    const msg = req.body?.message;
    if (msg) {
        try {
            await handle(msg);
        } catch (err) {
            console.error(err);
            await reply(msg, `⚠️ 처리 중 오류가 발생했어요: ${err.message}`);
        }
    }
    // Telegram 은 200 이 아니면 같은 업데이트를 계속 재전송하므로 항상 200 응답
    res.status(200).json({ ok: true });
}

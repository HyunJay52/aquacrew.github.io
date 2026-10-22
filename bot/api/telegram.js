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
    "📝 새 글",
    "• 훈련일지 텍스트를 그대로 보내주세요. (사진 1장 + 캡션으로 보내면 그 사진이 썸네일)",
    "",
    "✏️ 수정 — 봇 메시지('PR #번호', 배포 완료 알림, /글 검색 결과)에 답장",
    "• 텍스트로 수정 요청: 예) 날짜를 10월 19일로, 메인셋 인터벌 1'40\"로 수정",
    "• 사진 추가: 사진 전송 / 썸네일 변경: 사진 + 캡션 '썸네일'",
    "• /사진 — 사진 번호 목록",
    "• /삭제 2 3 — 2, 3번 사진 삭제",
    "• /썸네일 2 — 2번 사진을 썸네일로",
    "• 사진 + 캡션 '/교체 2' — 2번 사진 교체",
    "",
    "🔎 /글 2026-10-18 — 예전 글 찾기 (날짜 또는 제목 일부)",
    "",
    "모든 변경은 PR 로 올라가고, GitHub 에서 검수 후 Merge 하면 배포·알림됩니다.",
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

// 사이트에 올릴 수 있는 이미지 형식. SVG 는 스크립트를 담을 수 있어 받지 않는다
const IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
};

// 사진(압축 전송) 또는 이미지 파일(원본 전송)에서 file_id 와 확장자를 꺼낸다
function imageOf(msg) {
    if (msg.photo?.length) return { file_id: msg.photo[msg.photo.length - 1].file_id, ext: ".jpg" };
    const ext = IMAGE_TYPES[msg.document?.mime_type];
    return ext ? { file_id: msg.document.file_id, ext } : null;
}

// 답장한 봇 메시지에서 수정 대상을 찾는다: 'PR #번호' 또는 '[post:폴더]'
function targetOf(replied) {
    const text = replied?.text ?? replied?.caption ?? "";
    const pr = text.match(/PR #(\d+)/)?.[1];
    if (pr) return { pr: Number(pr) };
    const post = text.match(/\[post:([A-Za-z0-9_-]+)\]/)?.[1];
    return post ? { post } : null;
}

const numbersOf = (s) => (s.match(/\d+/g) ?? []).map(Number).slice(0, 20);

// 답장 내용으로 수정 종류를 정한다
function editOf(text, image) {
    if (image) {
        const replace = text.match(/^\/교체\s*(\d+)/);
        if (replace) return { op: "replace", indexes: [Number(replace[1])] };
        return { op: text.includes("썸네일") ? "thumbnail" : "add" };
    }
    if (text.startsWith("/사진")) return { op: "list" };
    if (text.startsWith("/삭제")) {
        const indexes = numbersOf(text);
        return indexes.length ? { op: "delete", indexes } : { error: "삭제할 사진 번호를 적어주세요. 예) /삭제 2" };
    }
    if (text.startsWith("/썸네일")) {
        const [n] = numbersOf(text);
        return n ? { op: "thumbnail_index", indexes: [n] } : { error: "사진 번호를 적어주세요. 예) /썸네일 2" };
    }
    if (text.startsWith("/")) return { error: "알 수 없는 명령이에요. /help 를 확인해주세요." };
    if (text.length < 2) return { error: "수정할 내용을 적어주세요." };
    return { op: "text", text };
}

async function handle(msg) {
    const allowed = (process.env.ALLOWED_CHAT_IDS ?? "").split(",").map((s) => s.trim());
    if (!allowed.includes(String(msg.chat.id))) {
        console.warn("ignored chat", msg.chat.id);
        return;
    }

    const text = (msg.text ?? msg.caption ?? "").trim();
    const image = imageOf(msg);

    if (text.startsWith("/start") || text.startsWith("/help")) {
        return reply(msg, HELP);
    }

    if (msg.document?.mime_type?.startsWith("image/") && !image) {
        return reply(msg, "jpg, png, webp, gif 이미지만 올릴 수 있어요. (HEIC 는 사진으로 보내거나 jpg 로 변환해주세요)");
    }

    const base = { chat_id: msg.chat.id, message_id: msg.message_id };

    if (text.startsWith("/글")) {
        const query = text.slice(2).trim();
        if (!query) return reply(msg, "예) /글 2026-10-18 또는 /글 송년회");
        await dispatch("telegram-edit", { ...base, op: "find", text: query });
        return;
    }

    const target = targetOf(msg.reply_to_message);
    if (target) {
        const edit = editOf(text, image);
        if (edit.error) return reply(msg, edit.error);
        await dispatch("telegram-edit", {
            ...base,
            ...target,
            ...edit,
            file_id: image?.file_id ?? null,
            ext: image?.ext ?? null,
        });
        if (edit.op === "text") return reply(msg, "✏️ 수정 요청을 접수했어요. 반영 후 알려드릴게요… (1분 내외)");
        return;
    }

    if (image && !text) {
        return reply(msg, "사진은 봇 메시지('PR #번호' 또는 배포 완료 알림)에 답장으로 보내주세요.");
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

import { TelegramClient } from './telegram';

export interface Env {
  DB: D1Database;
  MEDIA: R2Bucket;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  GITHUB_DISPATCH_TOKEN: string;
  GITHUB_REPO: string; // e.g. "hosoning/claudecodeedit"
}

async function dispatchRender(env: Env, episodeId: number) {
  await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      accept: 'application/vnd.github+json',
      'content-type': 'application/json',
      'user-agent': 'novel-channel-bot',
    },
    body: JSON.stringify({
      event_type: 'render_episode',
      client_payload: { episode_id: episodeId },
    }),
  });
}

async function handleCommand(
  text: string,
  chatId: number,
  env: Env,
  tg: TelegramClient
) {
  const [cmd, ...rest] = text.trim().split(/\s+/);
  const arg = rest.join(' ');

  if (cmd === '/start' || cmd === '/help') {
    await tg.sendMessage(
      chatId,
      '小說頻道控制台。指令：\n/status - 查看目前集數狀態\n/newscript <關鍵字> - 產生新一集大綱\n直接傳語音/音檔 - 綁定到最新一集的配音'
    );
    return;
  }

  if (cmd === '/status') {
    const { results } = await env.DB.prepare(
      'SELECT id, title, status, updated_at FROM episodes ORDER BY id DESC LIMIT 10'
    ).all();
    if (!results.length) {
      await tg.sendMessage(chatId, '目前沒有任何集數紀錄。');
      return;
    }
    const lines = results.map(
      (r: any) => `#${r.id} [${r.status}] ${r.title ?? '(未命名)'} - ${r.updated_at}`
    );
    await tg.sendMessage(chatId, lines.join('\n'));
    return;
  }

  if (cmd === '/newscript') {
    if (!arg) {
      await tg.sendMessage(chatId, '用法：/newscript <關鍵字或主題>');
      return;
    }
    const result = await env.DB.prepare(
      'INSERT INTO episodes (title, status, chat_id) VALUES (?, ?, ?)'
    )
      .bind(arg, 'script_pending', chatId)
      .run();
    const episodeId = result.meta.last_row_id;
    await tg.sendMessage(chatId, `已建立集數 #${episodeId}，主題：${arg}，開始生成劇本…`);
    await dispatchRender(env, episodeId as number);
    return;
  }

  await tg.sendMessage(chatId, '不認得這個指令，輸入 /help 看說明。');
}

async function handleVoiceUpload(
  fileId: string,
  chatId: number,
  env: Env,
  tg: TelegramClient
) {
  const { results } = await env.DB.prepare(
    "SELECT id FROM episodes WHERE status = 'script_ready' ORDER BY id DESC LIMIT 1"
  ).all();
  if (!results.length) {
    await tg.sendMessage(chatId, '找不到等待配音的集數（狀態需為 script_ready）。');
    return;
  }
  const episodeId = (results[0] as any).id as number;

  const file = await tg.getFile(fileId);
  if (!file.file_path) {
    await tg.sendMessage(chatId, '無法取得檔案路徑，請重新傳送。');
    return;
  }
  const bytes = await tg.downloadFile(file.file_path);
  const key = `episodes/${episodeId}/voice.mp3`;
  await env.MEDIA.put(key, bytes);

  await env.DB.prepare(
    "UPDATE episodes SET voice_r2_key = ?, status = 'voice_ready', updated_at = datetime('now') WHERE id = ?"
  )
    .bind(key, episodeId)
    .run();

  await tg.sendMessage(chatId, `配音已收到，綁定到 #${episodeId}，開始剪輯…`);
  await dispatchRender(env, episodeId);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== 'POST') {
      return new Response('ok');
    }

    const secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token');
    if (secret !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response('forbidden', { status: 403 });
    }

    const update = await request.json<any>();
    const tg = new TelegramClient(env.TELEGRAM_BOT_TOKEN);
    const message = update.message;
    if (!message) return new Response('ok');

    const chatId = message.chat.id;

    if (message.text?.startsWith('/')) {
      await handleCommand(message.text, chatId, env, tg);
    } else if (message.voice || message.audio || message.document) {
      const fileId =
        message.voice?.file_id ?? message.audio?.file_id ?? message.document?.file_id;
      await handleVoiceUpload(fileId, chatId, env, tg);
    }

    return new Response('ok');
  },
};

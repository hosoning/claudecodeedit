export interface TelegramFile {
  file_id: string;
  file_unique_id: string;
  file_size?: number;
  file_path?: string;
}

export class TelegramClient {
  constructor(private token: string) {}

  private api(method: string) {
    return `https://api.telegram.org/bot${this.token}/${method}`;
  }

  async sendMessage(chatId: number | string, text: string) {
    return fetch(this.api('sendMessage'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML' }),
    });
  }

  async getFile(fileId: string): Promise<TelegramFile> {
    const res = await fetch(this.api('getFile') + `?file_id=${fileId}`);
    const data = await res.json<{ result: TelegramFile }>();
    return data.result;
  }

  async downloadFile(filePath: string): Promise<ArrayBuffer> {
    const url = `https://api.telegram.org/file/bot${this.token}/${filePath}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`telegram file download failed: ${res.status}`);
    return res.arrayBuffer();
  }
}

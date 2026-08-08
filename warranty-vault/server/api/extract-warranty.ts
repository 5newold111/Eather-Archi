import type { VercelRequest, VercelResponse } from '@vercel/node';
import Anthropic from '@anthropic-ai/sdk';

/**
 * POST /api/extract-warranty
 *
 * 家電の保証書・レシート写真から情報を抽出するプロキシ。
 * ANTHROPIC_API_KEY はVercelの環境変数でのみ保持し、
 * リクエスト内容・画像はログに出力しない。
 */

const MAX_IMAGE_BASE64_LENGTH = 8_000_000; // ~6MB画像相当。クライアントは1400pxに縮小して送る
const ALLOWED_MEDIA_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const;
type AllowedMediaType = (typeof ALLOWED_MEDIA_TYPES)[number];

const EXTRACTION_INSTRUCTIONS = [
  'あなたは家電の保証書・レシートの写真から情報を読み取るアシスタントです。',
  '画像に写っている内容から、次の項目をできるだけ正確に読み取ってください。',
  '',
  '- name: 家電の名称（商品名がわかればそれを優先）',
  '- manufacturer: メーカー名',
  '- model: 型番',
  '- category: 最も近いカテゴリを1つ選ぶ',
  '- purchaseDate: 購入日。わかる場合は "YYYY-MM-DD" 形式',
  '- warrantyPeriodText: 保証期間の記載（例: "1年間"）',
  '- warrantyEndDate: 保証終了日を購入日と保証期間から確実に計算できる場合のみ "YYYY-MM-DD" 形式で。自信がない場合は null',
  '- retailer: 購入した店舗名',
  '- supportPhone: サポートセンター・お問い合わせ先の電話番号',
  '- serialNumber: 製造番号・保証書番号など',
  '- memo: 上記に当てはまらないが記載されている重要な情報（購入金額など）',
  '',
  '読み取れない項目は必ず null にしてください。推測で埋めないでください。',
].join('\n');

const nullableString = { anyOf: [{ type: 'string' }, { type: 'null' }] };

const EXTRACTION_SCHEMA = {
  type: 'object',
  properties: {
    name: nullableString,
    manufacturer: nullableString,
    model: nullableString,
    category: {
      anyOf: [
        {
          type: 'string',
          enum: [
            '冷蔵庫・キッチン家電',
            '洗濯機・生活家電',
            'エアコン・季節家電',
            'テレビ・AV機器',
            'PC・OA機器',
            'その他',
          ],
        },
        { type: 'null' },
      ],
    },
    purchaseDate: nullableString,
    warrantyPeriodText: nullableString,
    warrantyEndDate: nullableString,
    retailer: nullableString,
    supportPhone: nullableString,
    serialNumber: nullableString,
    memo: nullableString,
  },
  required: [
    'name',
    'manufacturer',
    'model',
    'category',
    'purchaseDate',
    'warrantyPeriodText',
    'warrantyEndDate',
    'retailer',
    'supportPhone',
    'serialNumber',
    'memo',
  ],
  additionalProperties: false,
} as const;

const client = new Anthropic();

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method Not Allowed' });
    return;
  }

  const { image, mediaType } = (req.body ?? {}) as {
    image?: unknown;
    mediaType?: unknown;
  };

  if (
    typeof image !== 'string' ||
    image.length === 0 ||
    image.length > MAX_IMAGE_BASE64_LENGTH ||
    typeof mediaType !== 'string' ||
    !ALLOWED_MEDIA_TYPES.includes(mediaType as AllowedMediaType)
  ) {
    res.status(400).json({ error: '読み取りに失敗しました' });
    return;
  }

  try {
    const response = await client.messages.create({
      // ハンドオフ指定「claude-sonnet-4-6 相当の最新モデル」→ 現行の最新Sonnet
      model: 'claude-sonnet-5',
      max_tokens: 2048,
      system: EXTRACTION_INSTRUCTIONS,
      output_config: {
        // 抽出タスクなので思考コストを抑えて応答速度を優先。精度が不足するなら "medium" へ
        effort: 'low',
        format: {
          type: 'json_schema',
          schema: EXTRACTION_SCHEMA,
        },
      },
      messages: [
        {
          role: 'user',
          content: [
            {
              type: 'image',
              source: {
                type: 'base64',
                media_type: mediaType as AllowedMediaType,
                data: image,
              },
            },
            {
              type: 'text',
              text: 'この写真から保証情報を読み取ってください。',
            },
          ],
        },
      ],
    });

    if (response.stop_reason === 'refusal') {
      res.status(422).json({ error: '読み取りに失敗しました' });
      return;
    }

    const textBlock = response.content.find((b) => b.type === 'text');
    if (!textBlock || textBlock.type !== 'text') {
      res.status(502).json({ error: '読み取りに失敗しました' });
      return;
    }

    res.status(200).json(JSON.parse(textBlock.text));
  } catch {
    // 画像・抽出内容はログに残さない
    res.status(502).json({ error: '読み取りに失敗しました' });
  }
}

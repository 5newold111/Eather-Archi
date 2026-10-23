import { useColorScheme } from 'react-native';
import type { CategoryLabel, WarrantyStatus } from './types';

/**
 * Apple のサイト / Human Interface Guidelines を参考にしたデザイントークン。
 * 白基調・#0071e3 のアクセント・角丸 18px のカード・システムフォント。
 */
export interface Theme {
  dark: boolean;
  background: string;
  card: string;
  elevated: string;
  text: string;
  secondaryText: string;
  tertiaryText: string;
  separator: string;
  accent: string;
  accentText: string;
  destructive: string;
  fill: string;
  statusColors: Record<WarrantyStatus, { fg: string; bg: string }>;
}

const light: Theme = {
  dark: false,
  background: '#f5f5f7',
  card: '#ffffff',
  elevated: '#ffffff',
  text: '#1d1d1f',
  secondaryText: '#6e6e73',
  tertiaryText: '#86868b',
  separator: '#d2d2d7',
  accent: '#0071e3',
  accentText: '#ffffff',
  destructive: '#d70015',
  fill: '#e8e8ed',
  statusColors: {
    active: { fg: '#248a3d', bg: '#e4f4e8' },
    expiring: { fg: '#a05a00', bg: '#fcf0dd' },
    expired: { fg: '#d70015', bg: '#fbe5e7' },
    unknown: { fg: '#6e6e73', bg: '#e8e8ed' },
  },
};

const dark: Theme = {
  dark: true,
  background: '#000000',
  card: '#1c1c1e',
  elevated: '#2c2c2e',
  text: '#f5f5f7',
  secondaryText: '#a1a1a6',
  tertiaryText: '#86868b',
  separator: '#38383a',
  accent: '#0a84ff',
  accentText: '#ffffff',
  destructive: '#ff453a',
  fill: '#2c2c2e',
  statusColors: {
    active: { fg: '#30d158', bg: '#0e2a16' },
    expiring: { fg: '#ffd60a', bg: '#2e2503' },
    expired: { fg: '#ff453a', bg: '#3a0d0a' },
    unknown: { fg: '#a1a1a6', bg: '#2c2c2e' },
  },
};

export function useTheme(): Theme {
  return useColorScheme() === 'dark' ? dark : light;
}

export const categoryColors: Record<CategoryLabel, string> = {
  '冷蔵庫・キッチン家電': '#34a853',
  '洗濯機・生活家電': '#0f9d9d',
  'エアコン・季節家電': '#ff9500',
  'テレビ・AV機器': '#af52de',
  'PC・OA機器': '#0071e3',
  'その他': '#8e8e93',
};

export const radius = { card: 18, control: 12, pill: 999 };

import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

export function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

export function nowISO() {
  return new Date().toISOString();
}

export function daysAgoISO(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString();
}

export function daysFromNowISO(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString();
}

export function slugify(text) {
  return String(text)
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

export function buildUTM({ source, medium = 'partner', campaign = 'referral', content = '' }) {
  const params = new URLSearchParams({
    utm_source: source,
    utm_medium: medium,
    utm_campaign: campaign,
    ...(content ? { utm_content: content } : {}),
  });
  return params.toString();
}

export function buildReferralLink({ baseUrl, refId, source = 'partner' }) {
  const utm = buildUTM({ source, content: refId });
  return `${baseUrl}/?ref=${encodeURIComponent(refId)}&${utm}`;
}

export async function writeOutput(relativePath, content) {
  const path = `output/${relativePath}`;
  await mkdir(dirname(path), { recursive: true });
  if (typeof content === 'string') {
    await writeFile(path, content, 'utf8');
  } else {
    await writeFile(path, JSON.stringify(content, null, 2), 'utf8');
  }
  return path;
}

export function weekNumber(date = new Date()) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
}

export function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function isCliEntry(importMeta, suffix) {
  // Returns true if this module was invoked directly via node, matching the suffix.
  const argv1 = process.argv[1] || '';
  return argv1.endsWith(suffix);
}

export function emailFooter() {
  return `
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0" />
    <p style="font-size:12px;color:#888;line-height:1.5">
      You're receiving this because you signed up at handeefriend.com.
      <a href="{{unsubscribe_url}}" style="color:#888">Unsubscribe anytime</a>.<br/>
      HandeeFriend &middot; West Kelowna, BC &middot;
      <a href="mailto:faydra@handeefriend.com" style="color:#888">faydra@handeefriend.com</a>
    </p>
  `;
}

export function randomCode(prefix = 'HF', len = 6) {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let s = '';
  for (let i = 0; i < len; i++) s += alphabet[Math.floor(Math.random() * alphabet.length)];
  return `${prefix}${s}`;
}

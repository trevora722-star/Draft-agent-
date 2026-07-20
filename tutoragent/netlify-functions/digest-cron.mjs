// Weekly digest on Netlify's scheduler. Netlify cron runs in UTC and has no
// timezone support: 01:00 UTC Monday = 5pm Sunday Pacific in winter (PST),
// 6pm in summer (PDT). Close enough for a study digest.
import { createStore } from '../src/store.js';
import { Repo } from '../src/repo.js';
import { sendDigest } from '../src/digest.js';

export default async function handler() {
  const store = createStore();
  const repo = new Repo(store);
  try {
    await store.init();
    const sent = await sendDigest(repo);
    console.log(`[digest] Weekly digest sent (${sent?.id || 'ok'})`);
  } catch (err) {
    console.error(`[digest] Failed: ${err.message}`);
  }
  return new Response('ok');
}

export const config = { schedule: '0 1 * * 1' };

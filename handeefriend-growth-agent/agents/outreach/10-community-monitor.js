import 'dotenv/config';
import { callClaude } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { sendEmail } from '../../lib/resend.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, nowISO, escapeHtml } from '../../lib/utils.js';

const AGENT_ID = 10;
const AGENT_NAME = 'community-monitor';
const MAX_PER_RUN = 10;

const SUBREDDITS = [
  'canadianhomeowners',
  'BritishColumbia',
  'FirstTimeHomeBuyer',
  'renovations',
];
const KEYWORDS = [
  'repair estimate',
  'renovation cost',
  'home inspection',
  'condition report',
  'contractor quote',
  'AI home',
];

const SYSTEM_PROMPT = `You are a knowledgeable Canadian homeowner responding to a forum question. You happen to know about HandeeFriend (handeefriend.com) and mention it only if it's directly relevant and helpful.

Rules:
- Answer the question fully and helpfully first. The answer is the value, not the product mention.
- Mention HandeeFriend only if the post is directly about repair estimates, condition reports, renovation planning, or AI home tools.
- Never sound like an ad. Sound like a neighbour who knows their stuff.
- BC-specific context where relevant (local prices, BC contractors, Lower Mainland vs Interior differences).
- Keep replies under 150 words.
- If HandeeFriend is mentioned: "I used HandeeFriend for something similar — free to try (handeefriend.com)"
- If not relevant: just give great advice. No product mention.

Output the reply only, no preamble.`;

let _redditToken = null;
let _redditTokenExpires = 0;

async function getRedditToken() {
  if (_redditToken && Date.now() < _redditTokenExpires) return _redditToken;
  const id = process.env.REDDIT_CLIENT_ID;
  const secret = process.env.REDDIT_CLIENT_SECRET;
  const username = process.env.REDDIT_USERNAME;
  const password = process.env.REDDIT_PASSWORD;
  if (!id || !secret || !username || !password) {
    throw new Error('Reddit credentials missing');
  }
  const auth = Buffer.from(`${id}:${secret}`).toString('base64');
  const body = new URLSearchParams({ grant_type: 'password', username, password });
  const res = await fetch('https://www.reddit.com/api/v1/access_token', {
    method: 'POST',
    headers: {
      Authorization: `Basic ${auth}`,
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent': process.env.REDDIT_USER_AGENT || 'handeefriend-growth-agent/0.1',
    },
    body: body.toString(),
  });
  if (!res.ok) throw new Error(`Reddit auth ${res.status}: ${await res.text()}`);
  const data = await res.json();
  _redditToken = data.access_token;
  _redditTokenExpires = Date.now() + (data.expires_in - 60) * 1000;
  return _redditToken;
}

async function searchReddit({ subreddit, query, limit = 5 }) {
  const token = await getRedditToken();
  const url = `https://oauth.reddit.com/r/${encodeURIComponent(subreddit)}/search?q=${encodeURIComponent(
    query,
  )}&restrict_sr=on&sort=new&t=week&limit=${limit}`;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      'User-Agent': process.env.REDDIT_USER_AGENT || 'handeefriend-growth-agent/0.1',
    },
  });
  if (!res.ok) {
    console.warn(`Reddit search failed: ${res.status}`);
    return [];
  }
  const data = await res.json();
  return (data.data?.children || []).map((c) => ({
    id: c.data.id,
    title: c.data.title,
    selftext: c.data.selftext || '',
    url: `https://reddit.com${c.data.permalink}`,
    subreddit: c.data.subreddit,
    created_utc: c.data.created_utc,
  }));
}

async function alreadyQueued(post_url) {
  const found = await nocodb.findOne('community_queue', `(post_url,eq,${post_url})`);
  return !!found;
}

async function draftReply(post) {
  const userContent = `Subreddit: r/${post.subreddit}
Title: ${post.title}
Body: ${post.selftext.slice(0, 1500)}

Draft a helpful reply.`;
  return callClaude({
    systemPrompt: SYSTEM_PROMPT,
    userContent,
    maxTokens: 800,
  });
}

function summaryEmail(items) {
  return `<div style="font-family:system-ui,sans-serif;max-width:640px">
    <h2>${items.length} community replies awaiting your approval</h2>
    <p>Open the review queue to approve, edit, or reject each one. Nothing is posted automatically.</p>
    <ul>${items
      .map(
        (i) => `<li><a href="${i.post_url}">${escapeHtml(i.post_title)}</a> (r/${escapeHtml(i.platform)})</li>`,
      )
      .join('')}</ul>
    <p><a href="${process.env.NETLIFY_SITE_URL || ''}/review-queue">Open review queue →</a></p>
  </div>`;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const posts = [];
    for (const sub of SUBREDDITS) {
      for (const kw of KEYWORDS) {
        if (posts.length >= MAX_PER_RUN * 3) break;
        try {
          const found = await searchReddit({ subreddit: sub, query: kw, limit: 3 });
          posts.push(...found);
        } catch (e) {
          logger.error(`reddit search failed for r/${sub} q="${kw}": ${e.message}`);
        }
      }
    }

    // Deduplicate by id, take top MAX_PER_RUN
    const seen = new Set();
    const unique = posts
      .filter((p) => {
        if (seen.has(p.id)) return false;
        seen.add(p.id);
        return true;
      })
      .slice(0, MAX_PER_RUN);

    logger.log(`${unique.length} unique candidate posts`);
    const queued = [];

    for (const post of unique) {
      logger.increment('records_processed');
      if (await alreadyQueued(post.url)) continue;

      try {
        const reply = await draftReply(post);
        const row = {
          platform: post.subreddit,
          post_url: post.url,
          post_title: post.title,
          post_excerpt: post.selftext.slice(0, 800),
          draft_reply: reply.trim(),
          status: 'pending',
          created_date: nowISO(),
        };
        if (!dryRun) {
          await nocodb.create('community_queue', row);
          logger.increment('records_created');
        }
        queued.push(row);
      } catch (e) {
        logger.error(`draft failed for ${post.url}: ${e.message}`);
      }
    }

    if (!dryRun && queued.length && process.env.TREVOR_EMAIL) {
      await sendEmail({
        to: process.env.TREVOR_EMAIL,
        subject: `${queued.length} community replies for review`,
        html: summaryEmail(queued),
      });
      logger.increment('emails_sent');
    }

    await logger.finish('success');
    return { queued: queued.length };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '10-community-monitor.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

import { Resend } from 'resend';
import cron from 'node-cron';
import { config, features } from './config.js';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtMinutes(min) {
  if (min >= 60) {
    const h = Math.floor(min / 60);
    const m = min % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  return `${min}m`;
}

// Clean, simple HTML email. No tracking pixels, no external assets.
export function renderDigestHtml(data) {
  const { range, minutesByCourse, coveredTopics, weakAreas, upcoming, quizzes } = data;

  const timeRows = Object.entries(minutesByCourse)
    .sort((a, b) => b[1] - a[1])
    .map(([course, min]) => `<tr><td style="padding:4px 12px 4px 0">${esc(course)}</td><td style="padding:4px 0"><strong>${fmtMinutes(min)}</strong></td></tr>`)
    .join('');

  const topicsList = [...new Set(coveredTopics.map((t) => `${t.course}: ${t.topic}`))]
    .map((t) => `<li>${esc(t)}</li>`)
    .join('');

  const weakList = weakAreas.map((w) => `<li>${esc(w.course)}: <strong>${esc(w.topic)}</strong></li>`).join('');

  const upcomingRows = upcoming
    .map(
      (d) =>
        `<tr><td style="padding:4px 12px 4px 0;white-space:nowrap">${esc(d.date)}</td>` +
        `<td style="padding:4px 12px 4px 0">${esc(d.course)}</td>` +
        `<td style="padding:4px 0">${esc(d.title)} <span style="color:#8a7a68">(${esc(d.type)}${d.weight_pct ? `, ${esc(d.weight_pct)}%` : ''})</span></td></tr>`
    )
    .join('');

  const avgScore = quizzes.length
    ? Math.round(quizzes.reduce((s, q) => s + Number(q.score_pct || 0), 0) / quizzes.length)
    : null;

  const focus = [];
  for (const w of weakAreas.slice(0, 2)) focus.push(`Drill <strong>${esc(w.topic)}</strong> (${esc(w.course)}) in Quiz Me — aim for two short sessions.`);
  const bigNext = upcoming.find((d) => Number(d.weight_pct) >= 15) || upcoming[0];
  if (bigNext) focus.push(`Start prepping for the ${esc(bigNext.course)} ${esc(bigNext.type)} on ${esc(bigNext.date)} — work backward from the date.`);
  if (!focus.length) focus.push('Keep the streak going — pick the topic marked "current" in each course and do one quiz session each.');

  const section = (title, body) =>
    `<h2 style="font-size:15px;margin:24px 0 8px;color:#5a4632">${title}</h2>${body}`;

  return `<!doctype html><html><body style="margin:0;padding:0;background:#faf6f0">
  <div style="max-width:560px;margin:0 auto;padding:24px 20px;font-family:Georgia,'Times New Roman',serif;color:#3d3226;line-height:1.5">
    <h1 style="font-size:20px;margin:0 0 4px">📚 Your week in review</h1>
    <p style="margin:0 0 16px;color:#8a7a68;font-size:13px">${esc(range.from)} → ${esc(range.to)}</p>

    ${section('Time studied', timeRows ? `<table style="font-size:14px;border-collapse:collapse">${timeRows}</table>` : '<p style="font-size:14px;margin:0">No study sessions logged this week — next week is a fresh start.</p>')}

    ${section('Topics you worked on', topicsList ? `<ul style="font-size:14px;margin:0;padding-left:20px">${topicsList}</ul>` : '<p style="font-size:14px;margin:0">None logged yet.</p>')}

    ${avgScore != null ? section('Quiz average', `<p style="font-size:14px;margin:0"><strong>${avgScore}%</strong> across ${quizzes.length} quiz${quizzes.length === 1 ? '' : 'zes'}.</p>`) : ''}

    ${section('Weak areas to shore up', weakList ? `<ul style="font-size:14px;margin:0;padding-left:20px">${weakList}</ul>` : '<p style="font-size:14px;margin:0">Nothing flagged — nice.</p>')}

    ${section('Coming up in the next 14 days', upcomingRows ? `<table style="font-size:14px;border-collapse:collapse">${upcomingRows}</table>` : '<p style="font-size:14px;margin:0">Nothing on the calendar. If that seems wrong, upload your course outlines in “Set Up My Courses”.</p>')}

    ${section('Suggested focus for next week', `<ul style="font-size:14px;margin:0;padding-left:20px">${focus.map((f) => `<li>${f}</li>`).join('')}</ul>`)}

    <p style="margin:28px 0 0;font-size:12px;color:#8a7a68">Sent by your TutorAgent. All of your data stays in Canada. 🍁</p>
  </div></body></html>`;
}

export async function sendDigest(repo) {
  if (!features.email) throw new Error('Email is not configured (missing RESEND_API_KEY or DIGEST_TO_EMAIL)');
  const data = await repo.digestData();
  const html = renderDigestHtml(data);
  const resend = new Resend(config.resendApiKey);
  const { data: sent, error } = await resend.emails.send({
    from: config.digestFromEmail,
    to: config.digestToEmail,
    subject: `📚 Study digest — week of ${data.range.from}`,
    html,
  });
  if (error) throw new Error(`Resend error: ${error.message || JSON.stringify(error)}`);
  return sent;
}

// Weekly cron: Sunday 5pm Pacific.
export function scheduleWeeklyDigest(repo, log = console) {
  if (!features.email) {
    log.warn('[digest] Weekly digest cron not scheduled — email is not configured.');
    return null;
  }
  return cron.schedule(
    '0 17 * * 0',
    async () => {
      try {
        const sent = await sendDigest(repo);
        log.log(`[digest] Weekly digest sent (${sent?.id || 'ok'})`);
      } catch (err) {
        log.error(`[digest] Failed to send weekly digest: ${err.message}`);
      }
    },
    { timezone: 'America/Vancouver' }
  );
}

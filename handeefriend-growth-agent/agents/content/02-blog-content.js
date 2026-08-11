import 'dotenv/config';
import { callClaude } from '../../lib/claude.js';
import { nocodb } from '../../lib/nocodb.js';
import { AgentLogger } from '../../lib/logger.js';
import { isCliEntry, slugify, writeOutput } from '../../lib/utils.js';

const AGENT_ID = 2;
const AGENT_NAME = 'blog-content';

const SYSTEM_PROMPT = `You are a senior SEO content writer for HandeeFriend (handeefriend.com), an AI-powered home repair and renovation platform for Canadian homeowners.

Write in a practical, knowledgeable tone. Imagine you're the smartest homeowner on the block — helpful, direct, no fluff. Use real Canadian context (BC prices, Canadian contractors, BC Building Code where relevant).

ALWAYS INCLUDE:
- H1 with target keyword near the start
- 5–7 H2 subheadings using long-tail keyword variations
- At least 2 internal links using anchor text like "get an AI repair estimate" or "try the Design Studio Render"
- A stats or data point in the first 100 words (even if estimated — label as "estimated by industry sources")
- FAQ section at the bottom (3 questions, schema-ready)
- CTA section: "Try HandeeFriend free — your first 3 reports are on us."
- Meta description (exactly 150–155 characters)

Target reading level: Grade 8. Short paragraphs. No walls of text.

Output format: a single complete HTML document fragment (no <html>/<head>/<body>) that begins with an HTML comment containing the meta description, then the article. Example:

<!-- META: 150-155 chars meta description here -->
<h1>...</h1>
<p>...</p>
<h2>...</h2>
...
<h2>FAQ</h2>
<script type="application/ld+json">{ "@context":"https://schema.org", "@type":"FAQPage", "mainEntity":[ ... ] }</script>
<div class="cta">Try HandeeFriend free — your first 3 reports are on us. <a href="https://handeefriend.com">Get started</a></div>`;

async function getBriefedRow(id) {
  if (id) {
    return nocodb.get('content_calendar', id);
  }
  const res = await nocodb.list('content_calendar', {
    where: '(content_type,eq,blog)~and(script_status,eq,briefed)',
    sort: '-CreatedAt',
    limit: 1,
  });
  const rows = res.list || res.records || [];
  return rows[0] || null;
}

export async function run(options = {}) {
  const dryRun = options.dryRun || process.argv.includes('--dry-run');
  const logger = new AgentLogger(AGENT_ID, AGENT_NAME, dryRun);

  try {
    const id =
      options.content_calendar_id ||
      process.argv
        .find((a) => a.startsWith('--id='))
        ?.split('=')[1];
    const row = await getBriefedRow(id);
    if (!row) throw new Error('No briefed blog row found in content_calendar');
    logger.log(`Writing post for keyword: "${row.keyword}" (id=${row.id})`);

    const brief = row.brief_json ? JSON.parse(row.brief_json) : {};
    const userContent = `Target keyword: ${row.keyword}
Recommended title: ${brief.recommended_title || row.keyword}
H2 outline: ${(brief.h2_outline || []).map((h) => `- ${h}`).join('\n')}
Required entities to mention: ${(brief.required_entities || []).join(', ')}
Internal links to weave in: ${JSON.stringify(brief.internal_links || [])}
Schema type: ${brief.schema_type || 'Article'}
Meta description suggestion: ${brief.meta_description_suggestion || ''}

Write the full ~900-word article now.`;

    const html = await callClaude({
      systemPrompt: SYSTEM_PROMPT,
      userContent,
      maxTokens: 4500,
    });

    logger.increment('records_processed');

    const slug = slugify(brief.recommended_title || row.keyword);

    if (!dryRun) {
      await nocodb.update('content_calendar', row.id, {
        script_content: html,
        script_status: 'draft',
      });
      logger.increment('records_created');

      const path = await writeOutput(`blog/${slug}.html`, html);
      logger.log(`HTML written to ${path}`);
    } else {
      console.log(html.slice(0, 800) + '\n...[truncated]');
    }

    await logger.finish('success');
    return { id: row.id, slug };
  } catch (e) {
    logger.error(e.message, { stack: e.stack });
    await logger.finish('failed');
    throw e;
  }
}

if (isCliEntry(import.meta, '02-blog-content.js')) {
  run().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

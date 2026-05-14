// intelligence/report.js
//
// Render the final HTML report from bugs.json + statistics.json + analysis.md
// (+ executive summary from Claude).

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { executiveSummary } from './hypothesis.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const TEMPLATE_PATH = path.join(__dirname, '..', 'report', 'template.html');

const SEV_LIST = ['critical', 'high', 'medium', 'low', 'info'];

/** Escape a string for safe HTML interpolation. */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Replace {{TOKEN}} occurrences with values. Unknown tokens are left alone. */
function fillTemplate(html, values) {
  return html.replace(/\{\{([A-Z_]+)\}\}/g, (m, key) => {
    if (Object.prototype.hasOwnProperty.call(values, key)) {
      return values[key];
    }
    return m;
  });
}

/**
 * Tiny markdown -> HTML converter. We control the input (it's our own Claude
 * prompt output) so we only need a subset: headings, paragraphs, lists,
 * inline code, and bold/italic.
 *
 * @param {string} md
 */
function renderMarkdown(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const inlineFmt = (s) =>
    escapeHtml(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  while (i < lines.length) {
    const line = lines[i];
    if (/^###\s+/.test(line)) {
      out.push('<h3>' + inlineFmt(line.replace(/^###\s+/, '')) + '</h3>');
      i++;
      continue;
    }
    if (/^##\s+/.test(line)) {
      out.push('<h2>' + inlineFmt(line.replace(/^##\s+/, '')) + '</h2>');
      i++;
      continue;
    }
    if (/^#\s+/.test(line)) {
      out.push('<h2>' + inlineFmt(line.replace(/^#\s+/, '')) + '</h2>');
      i++;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push('<li>' + inlineFmt(lines[i].replace(/^\s*[-*]\s+/, '')) + '</li>');
        i++;
      }
      out.push('<ul>' + items.join('') + '</ul>');
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push('<li>' + inlineFmt(lines[i].replace(/^\s*\d+\.\s+/, '')) + '</li>');
        i++;
      }
      out.push('<ol>' + items.join('') + '</ol>');
      continue;
    }
    if (/^\s*$/.test(line)) {
      i++;
      continue;
    }
    const para = [line];
    i++;
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^#{1,3}\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    out.push('<p>' + inlineFmt(para.join(' ')) + '</p>');
  }
  return out.join('\n');
}

/**
 * Build the bug list HTML. Each bug is a <details> with severity chip,
 * descriptions, repro steps, evidence, suggested fix, and a confidence bar.
 */
function renderBugs(bugs) {
  if (!bugs.length) {
    return '<p class="text-slate-500 italic">No bugs after triage.</p>';
  }
  /** @type {Record<string, any[]>} */
  const grouped = {};
  for (const s of SEV_LIST) grouped[s] = [];
  for (const b of bugs) {
    (grouped[b.final_severity] || grouped.info).push(b);
  }
  const sections = [];
  for (const sev of SEV_LIST) {
    const list = grouped[sev];
    if (!list.length) continue;
    sections.push(
      `<h3 class="text-sm font-bold uppercase tracking-wider mt-6 mb-2 text-slate-600">` +
        `<span class="chip sev-${sev}">${sev}</span> · ${list.length} bug${list.length === 1 ? '' : 's'}</h3>`,
    );
    for (const b of list) {
      sections.push(renderBugCard(b));
    }
  }
  return sections.join('\n');
}

function renderBugCard(b) {
  const screenshots = (b.evidence?.screenshots || []).slice(0, 4);
  const repro = (b.repro_steps || []).map((s) => `<li>${escapeHtml(s)}</li>`).join('');
  const consoleLogs = (b.evidence?.console_logs || [])
    .slice(0, 3)
    .map((s) => `<code class="block text-xs my-1 truncate">${escapeHtml(s)}</code>`)
    .join('');
  const network = (b.evidence?.network_failures || [])
    .slice(0, 3)
    .map((s) => `<code class="block text-xs my-1 truncate">${escapeHtml(s)}</code>`)
    .join('');
  const routes = (b.affected_routes || [])
    .slice(0, 6)
    .map(
      (u) =>
        `<a class="text-xs text-blue-600 underline mr-2 break-all" href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u)}</a>`,
    )
    .join('');
  const confidencePct = Math.round((b.confidence ?? 0) * 100);
  const sev = b.final_severity;
  return `
<details data-bug-id="${escapeHtml(b.id)}" class="bg-white border border-slate-200 rounded-lg">
  <summary class="px-4 py-3 flex items-start gap-3 hover:bg-slate-50 rounded-lg">
    <svg class="chev shrink-0 mt-1" width="10" height="10" viewBox="0 0 10 10"><path d="M2 0 L8 5 L2 10 Z" fill="#64748b"/></svg>
    <span class="chip sev-${sev} shrink-0">${sev}</span>
    <div class="flex-1 min-w-0">
      <div class="font-semibold text-slate-800 break-words">${escapeHtml(b.title)}</div>
      <div class="text-xs text-slate-500 mt-0.5">${b.affected_routes.length} route${b.affected_routes.length === 1 ? '' : 's'} · confidence ${confidencePct}%</div>
    </div>
  </summary>
  <div class="px-4 pb-5 pt-2 space-y-4 text-sm">
    <p class="text-slate-700"><span class="font-semibold text-slate-800">Executive: </span>${escapeHtml(b.executive_description)}</p>
    <p class="text-slate-700"><span class="font-semibold text-slate-800">Technical: </span>${escapeHtml(b.technical_description)}</p>
    ${repro ? `<div><div class="font-semibold text-slate-800 mb-1">Reproduction</div><ol class="list-decimal ml-5 text-slate-700">${repro}</ol></div>` : ''}
    ${routes ? `<div><div class="font-semibold text-slate-800 mb-1">Affected routes</div>${routes}</div>` : ''}
    ${consoleLogs ? `<div><div class="font-semibold text-slate-800 mb-1">Console</div>${consoleLogs}</div>` : ''}
    ${network ? `<div><div class="font-semibold text-slate-800 mb-1">Failed requests</div>${network}</div>` : ''}
    ${
      screenshots.length
        ? `<div><div class="font-semibold text-slate-800 mb-1">Screenshots</div><div class="flex flex-wrap gap-2">${screenshots
            .map(
              (s) =>
                `<img src="${escapeHtml(s)}" alt="" onclick="openLightbox('${escapeHtml(s)}')" class="h-24 w-auto cursor-zoom-in rounded border border-slate-200"/>`,
            )
            .join('')}</div></div>`
        : ''
    }
    <div><div class="font-semibold text-slate-800 mb-1">Suggested fix</div><p class="text-slate-700">${escapeHtml(b.suggested_fix)}</p></div>
    <div><div class="font-semibold text-slate-800 mb-1">Severity reasoning</div><p class="text-slate-700">${escapeHtml(b.severity_reasoning)}</p></div>
    <div>
      <div class="flex items-center justify-between text-xs text-slate-500 mb-1"><span class="font-semibold text-slate-800">Confidence</span><span>${confidencePct}%</span></div>
      <div class="w-full bg-slate-100 rounded h-2"><div class="h-2 rounded bg-emerald-500" style="width:${confidencePct}%"></div></div>
    </div>
  </div>
</details>`;
}

/**
 * Render the full report.
 *
 * @param {object} input
 * @param {string} input.target
 * @param {string} input.runId
 * @param {string} input.runTs
 * @param {any[]} input.bugs
 * @param {any[]} input.findings
 * @param {object} input.statistics
 * @param {string} input.analysisMarkdown
 * @param {string} [input.executiveSummaryText]
 * @param {number} [input.routesVisited]
 * @param {number} [input.semanticMerges]
 * @returns {Promise<string>} The fully rendered HTML.
 */
export async function renderReport(input) {
  const {
    target,
    runId,
    runTs,
    bugs,
    findings,
    statistics,
    analysisMarkdown,
    executiveSummaryText,
    routesVisited = 0,
    semanticMerges = 0,
  } = input;
  const tpl = await fs.readFile(TEMPLATE_PATH, 'utf8');
  const sev = statistics.by_severity || {};
  const totalFindings = statistics.totals?.findings ?? findings.length;
  const totalBugs = statistics.totals?.bugs ?? bugs.length;
  const dedupRatio = statistics.totals?.dedup_ratio ?? 0;

  const execHtml = renderMarkdown(
    executiveSummaryText || '_Executive summary unavailable._',
  );
  const analysisHtml = renderMarkdown(
    analysisMarkdown || '_Analysis unavailable._',
  );
  const bugListHtml = renderBugs(bugs);

  // The script payload powering charts is bound to a typed JSON island.
  const reportData = {
    statistics,
    findings: findings.map((f) => ({ url: f.url, category: f.category })),
    bugs: bugs.map((b) => ({
      id: b.id,
      title: b.title,
      final_severity: b.final_severity,
    })),
  };

  return fillTemplate(tpl, {
    TARGET: escapeHtml(target),
    RUN_ID: escapeHtml(runId),
    RUN_TS: escapeHtml(runTs),
    SEV_CRITICAL: String(sev.critical?.count || 0),
    SEV_HIGH: String(sev.high?.count || 0),
    SEV_MEDIUM: String(sev.medium?.count || 0),
    SEV_LOW: String(sev.low?.count || 0),
    SEV_INFO: String(sev.info?.count || 0),
    TOTAL_BUGS: String(totalBugs),
    TOTAL_FINDINGS: String(totalFindings),
    DEDUP_PCT: String(Math.round(dedupRatio * 100)),
    EXEC_SUMMARY_HTML: execHtml,
    ANALYSIS_HTML: analysisHtml,
    BUG_LIST_HTML: bugListHtml,
    ROUTES_VISITED: String(routesVisited),
    SEMANTIC_MERGES: String(semanticMerges),
    REPORT_DATA_JSON: JSON.stringify(reportData).replace(/</g, '\\u003c'),
  });
}

/**
 * File-level: read everything off disk and write report.html.
 *
 * @param {object} paths
 * @param {string} paths.target
 * @param {string} paths.runDir
 * @param {string} paths.bugsPath
 * @param {string} paths.findingsPath
 * @param {string} paths.statisticsPath
 * @param {string} paths.analysisPath
 * @param {string} paths.outPath
 * @param {boolean} [paths.skipExecutiveSummary]
 */
export async function renderReportFile({
  target,
  runDir,
  bugsPath,
  findingsPath,
  statisticsPath,
  analysisPath,
  outPath,
  skipExecutiveSummary = false,
}) {
  const [bugs, findings, statistics, analysisMarkdown] = await Promise.all([
    fs.readFile(bugsPath, 'utf8').then(JSON.parse),
    fs.readFile(findingsPath, 'utf8').then(JSON.parse),
    fs.readFile(statisticsPath, 'utf8').then(JSON.parse),
    fs.readFile(analysisPath, 'utf8'),
  ]);

  let summaryText = '';
  if (!skipExecutiveSummary && process.env.ANTHROPIC_API_KEY) {
    try {
      summaryText = await executiveSummary({ target, bugs, analysisMarkdown });
    } catch (err) {
      console.warn('[report] executive summary failed:', err.message);
    }
  }
  if (!summaryText) {
    const critical = bugs.filter((b) => b.final_severity === 'critical').length;
    const high = bugs.filter((b) => b.final_severity === 'high').length;
    summaryText =
      `Automated QA pass over ${target} produced ${bugs.length} deduplicated bug(s) ` +
      `from ${findings.length} raw observation(s). ${critical} critical, ${high} high. ` +
      `See the analysis section below for root-cause hypotheses and prioritization.`;
  }

  const html = await renderReport({
    target,
    runId: path.basename(runDir),
    runTs: new Date().toISOString(),
    bugs,
    findings,
    statistics,
    analysisMarkdown,
    executiveSummaryText: summaryText,
    routesVisited: Object.keys(statistics.by_route || {}).length,
    semanticMerges: 0,
  });
  await fs.writeFile(outPath, html);
  return outPath;
}

/**
 * Orchestrator: run the full intelligence pipeline.
 *
 * @param {object} opts
 * @param {string} opts.findingsPath
 * @param {string} [opts.inventoryPath]
 * @param {string} opts.runDir
 * @param {string} opts.target
 */
export async function runIntel({ findingsPath, inventoryPath, runDir, target }) {
  const { triageFile } = await import('./triage.js');
  const { statisticsFile } = await import('./statistics.js');
  const { analyzeFile } = await import('./hypothesis.js');

  const bugsPath = path.join(runDir, 'bugs.json');
  const statisticsPath = path.join(runDir, 'statistics.json');
  const analysisPath = path.join(runDir, 'analysis.md');
  const reportPath = path.join(runDir, 'report.html');

  console.log('[intel] triage…');
  const { stats: triageStats } = await triageFile(findingsPath, bugsPath);
  console.log(
    `[intel] triage: ${triageStats.total_findings} findings → ${triageStats.final_bugs} bugs (dedup ${Math.round(
      triageStats.dedup_ratio * 100,
    )}%).`,
  );

  console.log('[intel] statistics…');
  await statisticsFile({
    findingsPath,
    bugsPath,
    inventoryPath,
    outPath: statisticsPath,
  });

  console.log('[intel] hypothesis pass (Claude Opus)…');
  if (process.env.ANTHROPIC_API_KEY) {
    try {
      await analyzeFile({
        target,
        bugsPath,
        statisticsPath,
        findingsPath,
        outPath: analysisPath,
      });
    } catch (err) {
      console.warn('[intel] hypothesis failed:', err.message);
      await fs.writeFile(
        analysisPath,
        '## Root Cause Hypotheses\n_Analysis unavailable: ' + err.message + '_',
      );
    }
  } else {
    console.warn(
      '[intel] ANTHROPIC_API_KEY not set — emitting placeholder analysis.md.',
    );
    await fs.writeFile(
      analysisPath,
      '## Root Cause Hypotheses\n\n_Claude pass skipped (ANTHROPIC_API_KEY not set). See bugs.json and statistics.json for raw data._\n\n## Architectural Inferences\n\n_n/a_\n\n## Risk Surface Analysis\n\n_n/a_\n\n## Prioritization Recommendation\n\n_Sort bugs.json by final_severity._\n\n## Confidence Statement\n\n_n/a_\n',
    );
  }

  console.log('[intel] rendering report…');
  await renderReportFile({
    target,
    runDir,
    bugsPath,
    findingsPath,
    statisticsPath,
    analysisPath,
    outPath: reportPath,
  });
  console.log(`[intel] report → ${reportPath}`);
  return { bugsPath, statisticsPath, analysisPath, reportPath };
}

export const _internal = { renderMarkdown, escapeHtml, fillTemplate };

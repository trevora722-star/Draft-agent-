// Course-outline PDF -> structured enrichment data.
// pdf-parse is imported from its lib path to avoid the package's debug-mode
// side effect when the module is loaded without a parent.
import pdfParse from 'pdf-parse/lib/pdf-parse.js';
import { jsonCall } from './claude.js';

export async function extractPdfText(buffer) {
  const parsed = await pdfParse(buffer);
  return (parsed.text || '').trim();
}

const EXTRACTION_SYSTEM = `You extract structured data from university course outlines (syllabi).
Return ONLY a JSON object — no prose, no markdown fences. Schema:
{
  "course_name": string|null,        // e.g. "CHEM 1110"
  "instructor": string|null,
  "term": string|null,               // e.g. "Fall 2026"
  "textbook_title": string|null,
  "textbook_edition": string|null,
  "grading_weights": {               // percent per component, keys lowercase, e.g.
    "final": 40, "midterm": 25, "labs": 20, "quizzes": 15
  } | null,
  "lab_report_format": string|null,  // instructor's stated lab-report format requirements, verbatim-ish summary
  "topics": [                        // topics by week, in order
    {"name": string, "week_number": number, "textbook_chapter": string|null}
  ],
  "key_dates": [                     // every dated assessment you can find
    {"type": "quiz"|"midterm"|"final"|"lab_report"|"assignment", "title": string,
     "date": "YYYY-MM-DD", "weight_pct": number|null}
  ]
}
Rules:
- Dates must be ISO YYYY-MM-DD. If the outline gives only "Week 5", omit that item from key_dates.
- If the year is missing, infer it from the term. If a field is truly absent, use null (or [] for arrays).
- topics: one entry per week where possible; merge sub-bullets into one name separated by "; ".
- Do not invent data that is not in the outline.`;

export async function extractOutline(pdfText) {
  const clipped = pdfText.slice(0, 60000); // course outlines are short; guard anyway
  const data = await jsonCall({
    system: EXTRACTION_SYSTEM,
    user: `Course outline text follows:\n\n---\n${clipped}\n---\n\nReturn the JSON object.`,
    maxTokens: 8000,
  });
  if (!data || typeof data !== 'object') throw new Error('Extraction returned no object');
  data.topics = Array.isArray(data.topics) ? data.topics : [];
  data.key_dates = Array.isArray(data.key_dates) ? data.key_dates : [];
  return data;
}

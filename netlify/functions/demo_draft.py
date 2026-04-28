"""POST /api/demo/draft — grant draft on Haiku 4.5 (no thinking).

Uses the fast model with thinking disabled and a modest max_tokens so the
whole call returns inside Netlify's 10s sync function timeout. For self-
hosted (non-serverless) deploys, the GrantWriter defaults to Opus 4.7 with
adaptive thinking; the demo override is applied here, not in the agent.
"""

import time

from _shared import err, ok, parse_json, safe_handler  # type: ignore[import-not-found]


@safe_handler
def handler(event, context):
    from npo_agent.agents import GrantWriter
    from npo_agent.config import get_settings
    from npo_agent.demo import _demo_tenant

    body = parse_json(event)
    funder_brief = body.get("funder_brief")
    if not isinstance(funder_brief, str) or not funder_brief.strip():
        return err(400, "Body must include a non-empty 'funder_brief'.")

    settings = get_settings()
    tenant = _demo_tenant()
    started = time.monotonic()
    draft = GrantWriter(tenant).draft(
        funder=body.get("funder", "BC Gaming Community Grants"),
        opportunity_title=body.get("opportunity_title", "Community Gaming Grant"),
        funder_brief=funder_brief,
        ask_amount=body.get("ask_amount"),
        model=settings.model_fast,
        use_thinking=False,
        max_tokens=4_000,
    )
    return ok({
        "draft_id": draft.id,
        "body": draft.body,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "input_tokens": draft.usage.input_tokens,
        "output_tokens": draft.usage.output_tokens,
    })

"""POST /api/demo/preview-scrub — server-side PII filter preview.

Lets the demo UI show, in real time, what the privacy filter strips before
any text reaches Claude. No tokens are spent here; this is pure Python.
"""

from _shared import err, ok, parse_json, safe_handler  # type: ignore[import-not-found]


@safe_handler
def handler(event, context):
    from npo_agent.privacy import scrub

    body = parse_json(event)
    text = body.get("text")
    if not isinstance(text, str):
        return err(400, "Body must be {'text': '...'}.")

    result = scrub(text)
    types = sorted({p.strip("[]").rsplit("_", 1)[0] for p in result.mapping.keys()})
    return ok({
        "scrubbed": result.text,
        "placeholder_count": len(result.mapping),
        "placeholder_types": types,
    })

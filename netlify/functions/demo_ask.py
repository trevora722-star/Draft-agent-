"""POST /api/demo/ask — Policy Navigator RAG Q&A on Haiku 4.5."""

from _shared import err, ok, parse_json, safe_handler  # type: ignore[import-not-found]


@safe_handler
def handler(event, context):
    from npo_agent.agents import PolicyNavigator
    from npo_agent.demo import _demo_tenant

    body = parse_json(event)
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return err(400, "Body must include a non-empty 'question'.")

    answer = PolicyNavigator(_demo_tenant()).ask(question)
    return ok({
        "answer": answer.answer,
        "citations": answer.citations,
    })

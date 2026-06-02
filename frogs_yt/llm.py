"""llm.py — draft reply text via an OpenAI-compatible API (DeepSeek by default).

The provider (base_url, model, api_key) is fully configurable from Settings, so
the same code talks to DeepSeek, OpenAI, or any compatible endpoint. When no API
key is set we return a clearly-labeled stub so the whole pipeline (queue / edit /
post / dedupe / throttle) is testable before a real key exists.
"""


class LLMError(Exception):
    """Any failure generating a reply (auth, rate limit, network, bad config)."""


STUB_PREFIX = "[stub reply — no LLM key set]"


def build_user_prompt(comment, video=None):
    """Compose the user-turn from a comment dict (+ optional video dict)."""
    author = comment.get("author") or "a viewer"
    text = (comment.get("text") or "").strip()
    title = (video or {}).get("title") if video else None
    ctx = f" on the video “{title}”" if title else ""
    return (
        f"A YouTube user named {author} left this comment{ctx}:\n\n"
        f"\"{text}\"\n\n"
        f"Write a single short reply to post publicly under their comment."
    )


def generate_reply(comment, video, cfg, *, temperature=None, nudge=None):
    """Return draft reply text for `comment`.

    cfg is a Config. If no LLM api_key is configured, returns a labeled stub.
    `temperature` overrides the configured value (used by 'regenerate').
    `nudge` (used by 'regenerate') is appended to the user prompt to vary output.
    Raises LLMError on a real API failure.
    """
    llm = cfg["llm"]
    if not llm.get("api_key"):
        author = comment.get("author", "there")
        return f"{STUB_PREFIX} Thanks for the comment, {author}! 🐸"

    try:
        from openai import OpenAI
    except ImportError as e:  # pragma: no cover - openai is in requirements
        raise LLMError("The 'openai' package is not installed.") from e

    user_prompt = build_user_prompt(comment, video)
    if nudge:
        user_prompt += f"\n\n({nudge})"

    temp = llm.get("temperature", 0.8) if temperature is None else temperature
    max_tokens = int(llm.get("max_tokens", 200))

    try:
        client = OpenAI(api_key=llm["api_key"], base_url=llm["base_url"])
        resp = client.chat.completions.create(
            model=llm["model"],
            messages=[
                {"role": "system", "content": cfg["system_prompt"]},
                {"role": "user", "content": user_prompt},
            ],
            temperature=float(temp),
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        raise LLMError(str(e)) from e

    return _clean(text)


def _clean(text):
    """Trim wrapping quotes / stray markdown the model sometimes adds."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"“'" and text[-1] in "\"”'":
        text = text[1:-1].strip()
    return text


def test_connection(cfg):
    """Run a one-shot generation to validate provider credentials.

    Returns the generated text on success; raises LLMError on failure.
    Used by the Settings 'Test LLM' button.
    """
    if not cfg.has_llm_key():
        raise LLMError("No LLM API key set — fill in the provider settings first.")
    sample = {"author": "TestUser", "text": "Omg this frog is so cute, where can I get one?"}
    return generate_reply(sample, {"title": "Cutest crochet frog ever"}, cfg)

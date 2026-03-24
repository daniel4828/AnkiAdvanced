# Step 02 — Multi-Provider AI (DeepSeek / GLM / Qwen / Claude)

**Branch:** `feat/ai-provider`
**Depends on:** nothing (can be done in parallel with Step 01)
**Blocks:** Step 09 (browse enrich), Step 10 (story modal)

---

## What to Read First

1. `ai.py` — full file (understand current structure)
2. `recovery/2026-03-24_multi-provider-ai.md` — main reference
3. `recovery/going_manually_through_chats.md` lines 1–100 — model list and default

---

## Goal

Refactor `ai.py` from Anthropic-only to a multi-provider routing system.
Add DeepSeek, Zhipu GLM, and Alibaba Qwen support via their OpenAI-compatible APIs.
Change the default model to `deepseek-chat`.

---

## Allowed Models

```python
ALLOWED_MODELS = {
    "glm-4-flash",       # Zhipu (free)
    "glm-4-air",         # Zhipu
    "deepseek-chat",     # DeepSeek (default)
    "qwen-turbo",        # Alibaba
    "claude-haiku-4-5-20251001",  # Anthropic
    "claude-sonnet-4-6",          # Anthropic
    "claude-opus-4-6",            # Anthropic
}

DEFAULT_MODEL = "deepseek-chat"
```

---

## API Keys Required (from environment variables)

- `ANTHROPIC_API_KEY` — existing
- `DEEPSEEK_API_KEY` — new
- `ZHIPU_API_KEY` (or `GLM_API_KEY`) — new
- `DASHSCOPE_API_KEY` (or `QWEN_API_KEY`) — new

Load with `os.environ.get(...)` — no hard-coded keys.

---

## Provider Routing Logic

```python
def _openai_client(model: str):
    """Return an OpenAI-compatible client for non-Claude models."""
    if model.startswith("glm-"):
        from openai import OpenAI
        return OpenAI(
            api_key=os.environ.get("ZHIPU_API_KEY", ""),
            base_url="https://open.bigmodel.cn/api/paas/v4/"
        )
    elif model.startswith("deepseek-"):
        from openai import OpenAI
        return OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1"
        )
    elif model.startswith("qwen-"):
        from openai import OpenAI
        return OpenAI(
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    raise ValueError(f"Unknown non-Claude model: {model}")


def _call_api(model: str, messages: list, max_tokens: int, purpose: str) -> str:
    """
    Route to correct provider, call API, log usage, return text response.
    Raises on error.
    """
    if model.startswith("claude-"):
        # Use existing Anthropic client
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        database.log_api_call(
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            purpose=purpose,
        )
        return msg.content[0].text.strip()
    else:
        client = _openai_client(model)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        choice = resp.choices[0]
        # Log (openai-compat usage)
        usage = getattr(resp, "usage", None)
        database.log_api_call(
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            purpose=purpose,
        )
        return choice.message.content.strip()
```

---

## Functions to Update

### `generate_story(cards, topic, max_hsk, model=DEFAULT_MODEL)`

Add `model` parameter (default `DEFAULT_MODEL`).
Replace direct Anthropic call with `_call_api(model, messages, max_tokens, purpose)`.
Keep the 3-attempt retry logic for missing words.

### `generate_character_info(char, pinyin, model=DEFAULT_MODEL)`

Add `model` parameter. Use `_call_api()`.

### `enrich_word(word, characters, model="claude-haiku-4-5-20251001")`

This one stays Claude Haiku by default (it's a precise structured task).
But accept `model` parameter for flexibility. Use `_call_api()`.

---

## New Dependency

Add `openai` to the project. Check if it's already installed:
```bash
pip show openai
```
If not present, add to requirements (or tell user to `pip install openai`).
**Do not add it to CLAUDE.md's "no external dependencies" list** — ask Daniel first
whether to add it to requirements.txt or pyproject.toml.

Actually: check the recovery docs — `openai` package is already used as the
OpenAI-compatible client for Zhipu/DeepSeek/Qwen. Confirm it's in requirements.

---

## How to Implement

1. `git checkout -b feat/ai-provider`
2. Edit `ai.py`:
   - Add `ALLOWED_MODELS`, `DEFAULT_MODEL` constants at top
   - Add `_openai_client(model)` helper
   - Add `_call_api(model, messages, max_tokens, purpose)` helper
   - Update `generate_story`, `generate_character_info`, `enrich_word` to use `_call_api` and accept `model` param
3. Check/add `openai` dependency
4. Test: set `DEEPSEEK_API_KEY` in environment and generate a story with `deepseek-chat`
5. Commit and open PR

---

## Verification Checklist

- [ ] Server starts without errors
- [ ] Story generation works with `deepseek-chat`
- [ ] Story generation still works with `claude-haiku-4-5-20251001`
- [ ] `enrich_word` still works (used in browse)
- [ ] API calls are logged correctly in `api_cost_log`

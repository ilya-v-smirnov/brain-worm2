from openai import OpenAI
from config.settings import load_settings
import traceback


def get_openai_client() -> OpenAI:
    """
    OpenAI client with:
    - hard cap on number of paid requests per client
    - console logs for each request (model + payload size)
    - console logs for EXCEPTIONS (so we can see the real OpenAI error)
    """
    settings = load_settings()
    api_key = settings.get("openai_api_key")
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured.")

    MAX_CALLS = 20

    try:
        client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
    except TypeError:
        client = OpenAI(api_key=api_key)

    call_state = {"n": 0}

    def _bump(where: str, model: str | None, payload_chars: int | None = None) -> None:
        call_state["n"] += 1
        n = call_state["n"]
        print(
            f"[LLM] call #{n}/{MAX_CALLS} via {where} | model={model!r}"
            + (f" | payload_chars={payload_chars}" if payload_chars is not None else "")
        )
        if n > MAX_CALLS:
            raise RuntimeError(
                f"Safety stop: exceeded MAX_CALLS={MAX_CALLS} paid LLM calls. "
                "Aborting to prevent runaway costs."
            )

    def _print_exception(where: str, e: Exception) -> None:
        print(f"[LLM-ERROR] via {where}: {type(e).__name__}: {e}")
        tb = traceback.format_exc()
        print(tb)

    # ---- wrap chat.completions.create ----
    try:
        orig_cc_create = client.chat.completions.create

        def wrapped_cc_create(*args, **kwargs):
            where = "chat.completions.create"
            model = kwargs.get("model", None)

            payload_chars = None
            try:
                msgs = kwargs.get("messages", None)
                if isinstance(msgs, list):
                    payload_chars = sum(len(str(m.get("content", ""))) for m in msgs if isinstance(m, dict))
            except Exception:
                payload_chars = None

            _bump(where, model, payload_chars)

            try:
                return orig_cc_create(*args, **kwargs)
            except Exception as e:
                _print_exception(where, e)
                raise

        client.chat.completions.create = wrapped_cc_create
    except Exception as e:
        print(f"[LLM] WARNING: failed to wrap chat.completions.create: {type(e).__name__}: {e}")

    # ---- wrap responses.create (на всякий случай) ----
    try:
        if hasattr(client, "responses") and hasattr(client.responses, "create"):
            orig_r_create = client.responses.create

            def wrapped_r_create(*args, **kwargs):
                where = "responses.create"
                model = kwargs.get("model", None)

                payload_chars = None
                try:
                    inp = kwargs.get("input", None)
                    if inp is not None:
                        payload_chars = len(str(inp))
                except Exception:
                    payload_chars = None

                _bump(where, model, payload_chars)

                try:
                    return orig_r_create(*args, **kwargs)
                except Exception as e:
                    _print_exception(where, e)
                    raise

            client.responses.create = wrapped_r_create
    except Exception as e:
        print(f"[LLM] WARNING: failed to wrap responses.create: {type(e).__name__}: {e}")

    print(f"[LLM] OpenAI client created. MAX_CALLS={MAX_CALLS}")
    return client

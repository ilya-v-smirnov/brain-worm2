from openai import OpenAI
from config.settings import load_settings

def get_openai_client() -> OpenAI:
    settings = load_settings()
    api_key = settings.get("openai_api_key")
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured.")
    return OpenAI(api_key=api_key)

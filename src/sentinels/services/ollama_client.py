import logging
import time

from ollama import Client

from sentinels.council.config import ollama_host

log = logging.getLogger(__name__)

ollama = Client(host=ollama_host())

async def ollama_chat_completion(request_body: dict):
    model = request_body["model"]
    messages = request_body["messages"]
    
    # Call Ollama using the official client
    result = ollama.chat(
        model = model,
        messages=messages
    )
    
    # Log shape, never content. Prompts and completions are exactly what a log
    # collector would scrape, and on a tool whose point is that data stays local
    # that would be the leak.
    log.debug("ollama chat: model=%s messages=%d completion_chars=%d",
              model, len(messages), len(result["message"]["content"]))

    # Translate Ollama → OpenAI format
    return {
        "id": f"ollama-{model}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": result["message"]["role"],
                "content": result["message"]["content"],
            },
            "finish_reason": "stop"
        }]
    } 
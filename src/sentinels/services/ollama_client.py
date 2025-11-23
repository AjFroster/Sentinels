import time
from ollama import Client

ollama = Client(host="http://localhost:11434")

async def ollama_chat_completion(request_body: dict):
    model = request_body["model"]
    messages = request_body["messages"]
    
    # Call Ollama using the official client
    result = ollama.chat(
        model = model,
        messages=messages
    )
    
    print(result)
    
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
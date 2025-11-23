from fastapi import APIRouter
from sentinels.services.ollama_client import ollama_chat_completion

router = APIRouter()

@router.get("/sanity/check")
def sanity_check():
    return {"status": "sane in the membrane"}

@router.post("/chat/completions")
async def chat_completion(payload: dict):
    return await ollama_chat_completion(payload)
    

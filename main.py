from fastapi import FastAPI 
from sentinels.routes.chat import router as chat_router
from sentinels.routes.health import router as health_router

app = FastAPI()

app.include_router(chat_router, prefix="/v1")
app.include_router(health_router)

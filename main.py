from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sentinels.routes.chat import router as chat_router
from sentinels.routes.council import router as council_router
from sentinels.routes.health import router as health_router

WEB = Path(__file__).parent / "src" / "sentinels" / "web"

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Create the database and its tables before the first request can need them.
    from sentinels.routes.council import store
    await store.init()
    yield


app = FastAPI(
    title="Sentinels",
    description="A council that deliberates locally.",
    lifespan=lifespan,
)

app.include_router(chat_router, prefix="/v1")
app.include_router(health_router)
app.include_router(council_router)

# The desktop shell is plain files -- no build step, so the same directory can
# be loaded by a browser, an Electron window, or a Tauri webview unchanged.
app.mount("/app", StaticFiles(directory=WEB), name="app")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")

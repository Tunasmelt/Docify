from dotenv import load_dotenv

load_dotenv()  # must run before any module below reads env vars at import/construction time

from fastapi import FastAPI

from middleware.auth import JWTAuthMiddleware
from routes import documents, health, ingest

app = FastAPI(title="docify-api")

app.add_middleware(JWTAuthMiddleware)

app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(documents.router)

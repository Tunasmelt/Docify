from fastapi import FastAPI

from routes import health

app = FastAPI(title="docify-api")

app.include_router(health.router)

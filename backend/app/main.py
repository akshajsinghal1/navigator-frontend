from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import health, me, system_admin

app = FastAPI(title="Navigator Admin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174", "http://localhost:5175", "http://127.0.0.1:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(me.router)
app.include_router(system_admin.router)

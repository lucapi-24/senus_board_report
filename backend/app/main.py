from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import init_db
from app.core.seed import seed_from_disk
from app.api import financial


app = FastAPI(
    title="Senus PLC Board Report API",
    description="AI-native financial data platform for Board Reporting",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(financial.router)


@app.on_event("startup")
async def startup():
    await init_db()
    await seed_from_disk()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "senus-board-api"}
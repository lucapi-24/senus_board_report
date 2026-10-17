from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.seed import seed_from_disk, seed_users
from app.api import financial, documents, auth, metrics


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

app.include_router(auth.router)
app.include_router(financial.router)
app.include_router(documents.router)
app.include_router(metrics.router)


@app.on_event("startup")
async def startup():
    # Schema is applied by Alembic (see docker-compose api command) before the
    # server starts; here we only ensure the demo data is present.
    await seed_users()
    await seed_from_disk()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "senus-board-api"}
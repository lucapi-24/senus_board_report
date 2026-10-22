from app.core.database import async_session_maker
from app.models.financial import User
from sqlalchemy import select
import asyncio

async def main():
    async with async_session_maker() as s:
        res = await s.execute(select(User))
        for u in res.scalars():
            print(u.email, u.role, u.hashed_password)

asyncio.run(main())

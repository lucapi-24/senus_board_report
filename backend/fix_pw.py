from app.core.database import async_session_maker
from app.core.security import hash_password
from app.models.financial import User
from sqlalchemy import select
import asyncio

async def main():
    async with async_session_maker() as s:
        res = await s.execute(select(User))
        for u in res.scalars():
            newh = hash_password('senus123')
            u.hashed_password = newh
            print('fixed', u.email)
        await s.commit()

asyncio.run(main())

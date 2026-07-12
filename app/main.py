from fastapi import FastAPI
from app.routers.users import router as user_router

app = FastAPI()

app.include_router(user_router)

@app.get('/')
async def home():
    return {'message': 'success'}


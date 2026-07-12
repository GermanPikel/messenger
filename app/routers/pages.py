from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["pages"])


@router.get("/")
async def home():
    return RedirectResponse(url="/login")


@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html")


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/messenger/chats")
async def chats_page(request: Request):
    return templates.TemplateResponse(request, "chats.html")


@router.get("/messenger/chats/{chat_id}")
async def chat_page(request: Request, chat_id: int):
    return templates.TemplateResponse(request, "chat.html", {"chat_id": chat_id})

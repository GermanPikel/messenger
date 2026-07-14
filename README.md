# Мессенджер

Проект в рамках практики **VK Education** («Кейс 1. Мессенджер»).
Backend мессенджера с личными и групповыми чатами, обменом сообщениями в
реальном времени через WebSocket, историей с пагинацией и поиском по
сообщениям.

Авторы: **Пикель Герман** и **Усенко Тимофей**.

## Возможности (MVP)

- [x] Регистрация и логин: JWT (access + refresh)
- [x] Авторизация по Bearer-токену на всех защищённых эндпоинтах
- [x] Личные чаты 1‑на‑1
- [x] Групповые чаты с созданием и названием
- [x] Обмен сообщениями в реальном времени через WebSocket
- [x] История сообщений с пагинацией («вверх» / «вниз»)
- [x] Управление участниками группового чата (добавление/удаление, права
      администратора) — доступно только администратору
- [x] Поиск сообщений в рамках чата по подстроке

## Технологии

- **FastAPI** — веб-фреймворк, асинхронный API
- **PostgreSQL** + **SQLAlchemy 2.0** (async ORM, `asyncpg`)
- **Alembic** — миграции БД
- **Jinja2** — серверный рендеринг шаблонов (простой веб-интерфейс)
- **WebSocket** — доставка сообщений в реальном времени
- **JWT (PyJWT)** + **passlib/bcrypt** — аутентификация и хранение паролей

## Архитектура проекта

```
messenger/
├── app/
│   ├── models/       # модели SQLAlchemy (User, Chat, ChatMember, Message)
│   ├── routers/      # роутеры: /users, /chats, /ws, страницы (pages)
│   ├── schemas/      # Pydantic-схемы для валидации запросов/ответов
│   ├── static/        # CSS
│   ├── templates/    # Jinja2-шаблоны интерфейса
│   ├── migrations/   # миграции Alembic
│   ├── auth.py        # хэширование паролей, выдача и проверка JWT
│   ├── config.py      # переменные окружения, настройки
│   ├── database.py    # инициализация асинхронного движка/сессии SQLAlchemy
│   ├── db_depends.py  # DI-зависимость для получения сессии БД
│   └── main.py         # точка входа FastAPI-приложения
├── alembic.ini
└── requirements.txt
```

## Модель данных

- **users** — пользователи (`username`, `email`, `hashed_password`)
- **chats** — чаты (`title`, `chat_type`: `private`/`group`, `created_by_id`)
- **chat_members** — участники чата (`chat_id`, `user_id`, `is_admin`,
  `joined_at`, `left_at`); уникальность по паре `(chat_id, user_id)`
- **messages** — сообщения (`chat_id`, `sender_id`, `text`,
  `client_message_id`, `created_at`, `updated_at`, `deleted_at`)
  - индекс по `(chat_id, created_at)` для быстрой пагинации истории
  - уникальность по `(sender_id, client_message_id)` для идемпотентной
    отправки (защита от дублей при повторной отправке с клиента)

## Надёжность доставки сообщений (WebSocket)

| Механизм | Как реализовано |
|---|---|
| ACK | сервер подтверждает получение и сохранение сообщения (`message.ack`) |
| Идемпотентность | клиент генерирует `client_message_id` (UUID); повторная отправка с тем же id не создаёт дубль |
| Дедупликация | обеспечивается уникальным ограничением `(sender_id, client_message_id)` в БД |
| Порядок | сообщения сортируются по `id` / `created_at` в рамках чата |
| Восстановление истории | после переподключения клиент может догрузить пропущенные сообщения через REST `GET /chats/{chat_id}/messages` |

## API

Полная интерактивная документация (Swagger UI) доступна после запуска
приложения по адресу `/docs` (ReDoc — `/redoc`).

| Метод | Эндпоинт | Назначение |
|---|---|---|
| POST | `/users/` | Регистрация пользователя |
| POST | `/users/token` | Вход (OAuth2 password flow), выдача access/refresh токенов |
| POST | `/users/refresh-token` | Обновление токенов по refresh-токену |
| GET | `/chats/` | Список чатов текущего пользователя |
| POST | `/chats/private` | Создать личный чат и отправить первое сообщение |
| POST | `/chats/group` | Создать групповой чат |
| GET | `/chats/{chat_id}` | Информация о чате |
| GET | `/chats/{chat_id}/messages` | История сообщений с пагинацией (`before_id`/`after_id`, `limit`) |
| GET | `/chats/{chat_id}/messages/search` | Поиск сообщений по подстроке (`query`) |
| GET | `/chats/{chat_id}/members` | Список участников чата |
| POST | `/chats/{chat_id}/members` | Добавить участника в группу (только админ) |
| DELETE | `/chats/{chat_id}/members/{user_id}` | Удалить участника из группы (только админ) |
| PATCH | `/chats/{chat_id}/members/{user_id}/admin` | Назначить участника администратором (только админ) |
| WS | `/ws/chats/{chat_id}` | Real-time обмен сообщениями |

Все эндпоинты `/chats/*` требуют авторизации (`Authorization: Bearer
<access_token>`).

### WebSocket

Подключение: `ws://<host>/ws/chats/{chat_id}?token=<access_token>`

Отправка сообщения клиентом:

```json
{
  "type": "message.send",
  "client_message_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "text": "привет!"
}
```

Ответ сервера отправителю (подтверждение):

```json
{
  "type": "message.ack",
  "client_message_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "message_id": 42,
  "status": "saved",
  "message": { "...": "..." }
}
```

Всем участникам чата (включая отправителя) рассылается событие:

```json
{ "type": "message", "id": 42, "chat_id": 1, "sender_id": 3, "text": "привет!", "created_at": "..." }
```

## Установка и запуск

### 1. Клонирование репозитория

```bash
git clone https://github.com/GermanPikel/messenger.git
cd messenger
```

### 2. Виртуальное окружение и зависимости

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. База данных

Поднимите PostgreSQL (например, через Docker):

```bash
docker run --name messenger-db -e POSTGRES_USER=messenger_1 \
  -e POSTGRES_PASSWORD=pgpass777 -e POSTGRES_DB=messenger_db \
  -p 5432:5432 -d postgres:16
```

### 4. Переменные окружения

Создайте файл `.env` в корне проекта:

```env
SECRET_KEY=<секретный ключ для подписи JWT>
```

> `SECRET_KEY` обязателен — без него приложение не сможет подписывать и
> проверять JWT-токены. Сгенерировать можно, например, командой
> `openssl rand -hex 32`.

### 5. Миграции

```bash
alembic upgrade head
```

### 6. Запуск приложения

```bash
uvicorn app.main:app --reload
```

Приложение будет доступно на `http://127.0.0.1:8000`, веб-интерфейс — на
`http://127.0.0.1:8000/login`, документация API — на
`http://127.0.0.1:8000/docs`.

## Разделение обязанностей

**Пикель Герман:**
- Проектирование БД
- Регистрация и логин с использованием JWT (access + refresh)
- Валидация данных
- Взаимодействие с чатами + авторизация
- Поиск внутри чата по строке

**Усенко Тимофей:**
- Взаимодействие пользователей по WebSocket + авторизация
- Администрирование групповых чатов
- Пользовательский интерфейс

"""
Web Warriors Survival Quest — Backend API
FastAPI + SQLite (можно заменить на PostgreSQL)
Запуск: pip install fastapi uvicorn aiofiles
        uvicorn server:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import json
import hashlib
import time
import os

app = FastAPI(title="Web Warriors API")

# CORS для Telegram Mini App (любые origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── База данных ──
DB_PATH = "web_warriors.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица игроков
    c.execute('''
        CREATE TABLE IF NOT EXISTS players (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            game_data TEXT DEFAULT '{}',
            score INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        )
    ''')
    
    # Таблица рейтинга (кэш для быстрой выдачи)
    c.execute('''
        CREATE TABLE IF NOT EXISTS leaderboard (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            score INTEGER,
            cards_count INTEGER,
            streak INTEGER,
            talent_level INTEGER,
            updated_at REAL
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ── Модели ──
class GameData(BaseModel):
    game_data: dict  # Полный JSON состояния игры

class PlayerProfile(BaseModel):
    telegram_id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    game_data: dict = {}
    score: int = 0

class LeaderboardEntry(BaseModel):
    telegram_id: str
    username: str
    score: int
    cards_count: int
    streak: int
    talent_level: int

# ── Вспомогательные функции ──
def get_player(telegram_id: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "telegram_id": row[0],
            "username": row[1],
            "first_name": row[2],
            "game_data": json.loads(row[3]) if row[3] else {},
            "score": row[4],
            "created_at": row[5],
            "updated_at": row[6]
        }
    return None

def save_player(telegram_id: str, game_data: dict, username: str = None, first_name: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = time.time()
    
    # Вычисляем счёт
    talents = game_data.get("talents", {})
    talent_sum = sum(t.get("unlockedLevel", 0) for t in talents.values())
    cards_count = len(game_data.get("collection", []))
    streak = game_data.get("streak", 0)
    score = talent_sum * 10 + cards_count * 5 + streak * 2
    
    existing = get_player(telegram_id)
    if existing:
        c.execute('''
            UPDATE players 
            SET game_data = ?, score = ?, username = COALESCE(?, username), 
                first_name = COALESCE(?, first_name), updated_at = ?
            WHERE telegram_id = ?
        ''', (json.dumps(game_data), score, username, first_name, now, telegram_id))
    else:
        c.execute('''
            INSERT INTO players (telegram_id, username, first_name, game_data, score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (telegram_id, username, first_name, json.dumps(game_data), score, now, now))
    
    # Обновляем кэш рейтинга
    c.execute('''
        INSERT OR REPLACE INTO leaderboard (telegram_id, username, score, cards_count, streak, talent_level, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (telegram_id, username or f"Выживший_{telegram_id[:6]}", score, cards_count, streak, talent_sum, now))
    
    conn.commit()
    conn.close()
    return {"status": "ok", "score": score}

def get_leaderboard(limit: int = 100):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM leaderboard ORDER BY score DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [
        {
            "telegram_id": row[0],
            "username": row[1],
            "score": row[2],
            "cards_count": row[3],
            "streak": row[4],
            "talent_level": row[5]
        }
        for row in rows
    ]

# ── API Endpoints ──

@app.get("/")
async def root():
    return {"status": "Web Warriors API Online", "version": "1.0.0"}

@app.post("/api/sync")
async def sync_data(data: GameData, request: Request):
    """
    Синхронизация данных игрока с сервером.
    Telegram Mini App передаёт initData в заголовке или параметрах.
    """
    # В реальном продакшене — проверка подписи Telegram
    # Сейчас для простоты берём telegram_id из данных игры
    telegram_id = data.game_data.get("telegram_id", "anonymous")
    username = data.game_data.get("username")
    first_name = data.game_data.get("first_name")
    
    result = save_player(telegram_id, data.game_data, username, first_name)
    return result

@app.get("/api/load/{telegram_id}")
async def load_data(telegram_id: str):
    """Загрузка данных игрока"""
    player = get_player(telegram_id)
    if not player:
        return {"status": "new_player", "game_data": None}
    return {"status": "ok", "game_data": player["game_data"], "score": player["score"]}

@app.get("/api/leaderboard")
async def leaderboard(limit: int = 100):
    """Глобальная таблица лидеров"""
    return get_leaderboard(limit)

@app.get("/api/player/{telegram_id}")
async def player_profile(telegram_id: str):
    """Профиль игрока"""
    player = get_player(telegram_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player

@app.delete("/api/player/{telegram_id}")
async def delete_player(telegram_id: str):
    """Удаление аккаунта (GDPR)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM players WHERE telegram_id = ?", (telegram_id,))
    c.execute("DELETE FROM leaderboard WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

# ── Хостинг статики (фронтенд) ──
@app.get("/app", response_class=HTMLResponse)
async def serve_app():
    """Отдаёт HTML фронтенда"""
    try:
        with open("vbrusentsev.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Файл vbrusentsev.html не найден. Проверь, что он в одной папке с server.py</h1>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
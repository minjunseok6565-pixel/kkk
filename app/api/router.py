from fastapi import APIRouter

from app.api.routes import core, sim, training, college, scouting, postseason, offseason, contracts, trades, news, game_saves

api_router = APIRouter()
api_router.include_router(core.router)
api_router.include_router(sim.router)
api_router.include_router(training.router)
api_router.include_router(college.router)
api_router.include_router(scouting.router)
api_router.include_router(postseason.router)
api_router.include_router(offseason.router)
api_router.include_router(contracts.router)
api_router.include_router(trades.router)
api_router.include_router(news.router)
api_router.include_router(game_saves.router)

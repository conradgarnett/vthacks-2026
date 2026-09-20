"""The place memory, for the panel: plain HTTP beside the socket.

The panel is a sighted helper's tool and needs no session. Every edit
answers with the whole memory so the panel redraws from what the server now
holds, and the client speaks the change. `bind()` tells the router where the
memory lives; until then every route says the memory is off.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.scene.places import PlaceMemory

router = APIRouter()
_memory: Callable[[], PlaceMemory | None] = lambda: None


def bind(getter: Callable[[], PlaceMemory | None]) -> None:
    """Where the routes find the memory, e.g. `lambda: app.state.places`."""
    global _memory
    _memory = getter


class PlaceName(BaseModel):
    name: str


class PlaceMerge(BaseModel):
    into: str


class SceneLink(BaseModel):
    place_id: str | None = None
    name: str | None = None


def _response() -> JSONResponse:
    memory = _memory()
    if memory is None:
        return JSONResponse({"enabled": False, "places": [], "unplaced": [], "current": None})
    return JSONResponse(memory.to_dict())


def _missing(what: str) -> JSONResponse:
    return JSONResponse({"error": f"no such {what}"}, status_code=404)


@router.get("/places")
async def places_index() -> JSONResponse:
    """Every remembered place with its views."""
    return _response()


@router.post("/places/{place_id}")
async def places_rename(place_id: str, body: PlaceName) -> JSONResponse:
    memory = _memory()
    if memory is None or memory.rename(place_id, body.name) is None:
        return _missing("place")
    return _response()


@router.delete("/places/{place_id}")
async def places_delete(place_id: str) -> JSONResponse:
    memory = _memory()
    if memory is None or memory.delete_place(place_id) is None:
        return _missing("place")
    return _response()


@router.delete("/places")
async def places_forget_all() -> JSONResponse:
    memory = _memory()
    if memory is not None:
        memory.forget_all()
    return _response()


@router.post("/places/{place_id}/merge")
async def places_merge(place_id: str, body: PlaceMerge) -> JSONResponse:
    """Every view of one place joins another; the first is gone."""
    memory = _memory()
    if memory is None or memory.merge(place_id, body.into) is None:
        return _missing("place")
    return _response()


@router.delete("/scenes/{scene_id}")
async def scenes_delete(scene_id: str) -> JSONResponse:
    memory = _memory()
    if memory is None or memory.delete_scene(scene_id) is None:
        return _missing("scene")
    return _response()


@router.post("/scenes/{scene_id}/place")
async def scenes_link(scene_id: str, body: SceneLink) -> JSONResponse:
    """Put a view into a place, or start a new place from it."""
    memory = _memory()
    if memory is None or memory.link(scene_id, body.place_id, body.name) is None:
        return _missing("scene or place")
    return _response()

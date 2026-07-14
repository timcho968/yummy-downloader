from pydantic import BaseModel
from typing import Optional


class AnimeSearchResult(BaseModel):
    id: int
    name: str
    url: str
    poster: Optional[str] = None
    rating: Optional[float] = None
    year: Optional[int] = None
    type: Optional[str] = None
    episodes_count: Optional[str] = None


class VideoEntry(BaseModel):
    video_id: int
    iframe_url: str
    number: str
    dubbing: str
    player: str
    player_id: int
    index: int


class AnimeDetail(BaseModel):
    id: int
    name: str
    url: str
    poster: Optional[str] = None
    rating: Optional[float] = None
    description: Optional[str] = None
    episodes: list[VideoEntry]


class StreamUrl(BaseModel):
    quality: str
    url: str
    cookies: dict[str, str] = {}
    headers: dict[str, str] = {}


class ResolveRequest(BaseModel):
    iframe_url: str
    player: str = ""


class ResolveResponse(BaseModel):
    streams: list[StreamUrl]


class DownloadRequest(BaseModel):
    url: str
    output_path: str
    quality: str = "720"
    referer: str = "https://yummyani.me/"
    cookies: dict[str, str] = {}
    extra_headers: dict[str, str] = {}
    client_id: str = ""
    iframe_url: str = ""
    player: str = ""


class DownloadProgress(BaseModel):
    status: str
    percent: Optional[float] = None
    speed: Optional[str] = None
    eta: Optional[str] = None
    filename: Optional[str] = None
    error: Optional[str] = None

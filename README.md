князь недоволен, света и связи нету. В условиях джуглей с ракетами я создал этот реп.

# YummyAnime Downloader

Скачивание аниме с [YummyAnime](https://ru.yummyani.me) через Kodik и Sibnet плееры.

## Возможности

- Поиск аниме по ссылке с YummyAnime
- Выбор озвучки, плеера и качества
- Выбор диапазона серий
- Параллельное скачивание серий
- Прогресс-бар в реальном времени (WebSocket)

## Требования

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (для склейки сегментов)
- Playwright (автоматически ставится через `pip install`)

## Установка и запуск

```bash
git clone https://github.com/YOUR_USERNAME/yummy-downloader.git
cd yummy-downloader
pip install -r requirements.txt
playwright install chromium
```

Запуск (Windows):

```bash
start.bat
```

Запуск (Linux/Mac):

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Открой http://localhost:8000 в браузере.

## Лицензия

MIT

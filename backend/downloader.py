import os
import re
import time
import subprocess
import tempfile
import logging
import threading
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

log = logging.getLogger("downloader")

logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}



def download_video(
    url: str,
    output_path: str,
    referer: str = "https://yummyani.me/",
    cookies: Optional[dict[str, str]] = None,
    extra_headers: Optional[dict[str, str]] = None,
    progress_callback: Optional[Callable] = None,
    iframe_url: str = "",
    player: str = "",
) -> str:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    log.info(f"Starting download: {output_path}")
    log.info(f"URL: {url[:120]}...")
    log.info(f"Referer: {referer}")
    log.info(f"Cookies: {list(cookies.keys()) if cookies else 'none'}")
    log.info(f"Extra headers: {list(extra_headers.keys()) if extra_headers else 'none'}")

    if progress_callback:
        progress_callback({
            "status": "downloading",
            "percent": 0,
            "filename": os.path.basename(output_path),
        })

    headers = {**HEADERS, "Referer": referer, "Origin": "https://yummyani.me"}
    if extra_headers:
        headers.update(extra_headers)
    cookie_dict = cookies or {}

    # Sibnet — Range-загрузка (быстрый путь), ffmpeg — fallback
    is_sibnet = "sibnet.ru" in url.lower()
    if is_sibnet:
        log.info("Sibnet detected — using range download")
        return _download_sibnet_with_retry(
            url, output_path, referer, cookie_dict, extra_headers or {},
            progress_callback, iframe_url,
        )

    log.info("Downloading via httpx (parallel segments)...")

    try:
        return _download_httpx(url, output_path, headers, cookie_dict, progress_callback)
    except Exception as e:
        log.warning(f"httpx failed ({e}), falling back to ffmpeg")
        return _download_ffmpeg(url, output_path, referer, cookie_dict, extra_headers or {}, progress_callback)


def _download_sibnet_with_retry(url, output_path, referer, cookies, extra_headers, progress_callback, iframe_url):
    last_err = None
    for attempt in range(3):
        try:
            if attempt > 0:
                # Повторная переадресация (антибот может блокировать старую ссылку)
                if iframe_url:
                    log.info(f"Sibnet retry {attempt}: re-resolving iframe...")
                    fresh = _resolve_sibnet_url(iframe_url)
                    if fresh:
                        url, extra_headers = fresh
                log.info(f"Sibnet retry {attempt}: waiting 8s before reconnect...")
                time.sleep(8)
            try:
                return _download_sibnet_range(url, output_path, referer, cookies, extra_headers, progress_callback)
            except Exception as e:
                log.warning(f"Sibnet range download failed ({e}), falling back to ffmpeg")
                return _download_ffmpeg(url, output_path, referer, cookies, extra_headers, progress_callback)
        except Exception as e:
            last_err = e
            log.warning(f"Sibnet attempt {attempt} failed: {e}")
    raise last_err


def _resolve_sibnet_url(iframe_url):
    try:
        from .sibnet_resolver import resolve_sibnet_streams
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            streams = loop.run_until_complete(resolve_sibnet_streams(iframe_url))
        finally:
            loop.close()
        if streams:
            s = streams[0]
            return s.url, s.headers
    except Exception as e:
        log.warning(f"Sibnet re-resolve failed: {e}")
    return None


def _download_sibnet_range(url, output_path, referer, cookies, extra_headers, progress_callback):
    # Быстрый путь: один Range-запрос (сервер отдаёт быстро, пока per-IP квота свежа).
    # Referer обязательно с домена sibnet.ru (yummyani.me блокируется -> 0 байт).
    extra = extra_headers or {}
    headers = {
        "User-Agent": extra.get("User-Agent", HEADERS["User-Agent"]),
        "Referer": extra.get("Referer", referer),
        "Accept": "*/*",
    }

    client = httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(connect=15, read=120, write=15, pool=15),
        verify=False,
    )

    tmp_path = output_path + ".part"
    try:
        # Узнаём размер файла через Range bytes=0-0 (HEAD сервер не любит -> 400)
        total_size = 0
        try:
            r0 = client.get(url, headers={"Range": "bytes=0-0"})
            cr = r0.headers.get("content-range", "")
            if r0.status_code == 206 and "/" in cr:
                total_size = int(cr.split("/")[-1])
        except Exception as e:
            log.warning(f"Sibnet size probe failed: {e}")

        log.info(f"Sibnet range download: total_size={total_size}")
        if progress_callback and total_size > 0:
            progress_callback({"status": "downloading", "percent": 0, "filename": os.path.basename(output_path)})

        downloaded = 0
        last_t = time.time()
        last_downloaded = 0
        with open(tmp_path, "wb") as f:
            range_hdr = {"Range": f"bytes=0-{total_size - 1}"} if total_size > 0 else {"Range": "bytes=0-"}
            with client.stream("GET", url, headers=range_hdr) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"Sibnet range request returned HTTP {r.status_code}")
                for chunk in r.iter_bytes(256 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_t >= 1.0:
                        dt = now - last_t
                        # скорость = прирост за интервал, а не накопленное
                        speed = ((downloaded - last_downloaded) / (1024 * 1024)) / dt if dt > 0 else 0
                        last_t = now
                        last_downloaded = downloaded
                        pct = min(99, downloaded * 100 // total_size) if total_size else 0
                        log.info(
                            f"Sibnet {os.path.basename(output_path)}: "
                            f"{downloaded // (1024 * 1024)}/{total_size // (1024 * 1024)} MB "
                            f"({pct}%) {speed:.2f} MB/s"
                        )
                        if progress_callback and total_size:
                            progress_callback({
                                "status": "downloading",
                                "percent": round(pct, 1),
                                "speed": f"{speed:.1f} MB/s",
                                "filename": os.path.basename(output_path),
                            })

        if total_size and downloaded < total_size * 0.9:
            raise RuntimeError(f"Sibnet incomplete: got {downloaded}/{total_size} bytes")

        os.replace(tmp_path, output_path)
    finally:
        try:
            client.close()
        except Exception:
            pass
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    _remux_faststart(output_path)

    sz = os.path.getsize(output_path)
    log.info(f"Download complete via Sibnet range: {output_path} ({sz} bytes)")
    if progress_callback:
        progress_callback({"status": "done", "percent": 100, "filename": os.path.basename(output_path)})
    return output_path


def _remux_faststart(path):
    # Быстрый локальный ремукс для +faststart (безопасно, при ошибке оставляем как есть)
    try:
        tmp = path + ".faststart.mp4"
        cmd = ["ffmpeg", "-y", "-i", path, "-c", "copy", "-movflags", "+faststart", tmp]
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        if p.wait(timeout=120) == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            log.info("Sibnet faststart remux done")
        else:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception as e:
        log.warning(f"Sibnet faststart remux skipped: {e}")


def _download_ffmpeg(url, output_path, referer, cookies, extra_headers, progress_callback):
    extra = extra_headers or {}
    has_referer = any(k.lower() == "referer" for k in extra)

    cmd = [
        "ffmpeg", "-y",
        "-timeout", "10000000",          # 10s connect/response timeout (microseconds)
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ]
    # Не дублируем Referer, если он уже задан в extra_headers
    if not has_referer:
        cmd.extend(["-referer", referer])
    cmd.extend(["-user_agent", extra.get("User-Agent", HEADERS["User-Agent"])])

    http_headers = f"Cookie: {'; '.join(f'{k}={v}' for k, v in cookies.items())}\r\n" if cookies else ""
    http_headers += f"Origin: https://yummyani.me\r\n"
    http_headers += f"Accept: */*\r\n"
    for k, v in extra.items():
        if k.lower() != "user-agent" and k.lower() != "referer":
            http_headers += f"{k}: {v}\r\n"
    # Если Referer пришёл в extra_headers — добавляем его явно
    if has_referer:
        http_headers += f"Referer: {extra.get('Referer', referer)}\r\n"
    if http_headers:
        cmd.extend(["-headers", http_headers])

    cmd.extend([
        "-i", url,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ])

    log.info("Trying ffmpeg direct download (connect timeout 10s)...")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
    )

    duration = [0.0]

    def _pump_stderr():
        try:
            for line in process.stderr:
                stripped = line.strip()
                if not stripped:
                    continue
                log.debug(f"ffmpeg: {stripped}")
                if not progress_callback:
                    continue
                if duration[0] == 0:
                    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", line)
                    if dur_match:
                        dh, dm, ds = dur_match.groups()
                        duration[0] = int(dh) * 3600 + int(dm) * 60 + int(ds)
                        log.info(f"Stream duration: {duration[0]}s")
                time_match = re.search(r"time=\s*(\d+):(\d+):(\d+)\.(\d+)", line)
                if time_match:
                    h, m, s, cs = time_match.groups()
                    current = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
                    if current > 0 and duration[0] > 0:
                        percent = min(99, (current / duration[0]) * 100)
                        progress_callback({
                            "status": "downloading",
                            "percent": round(percent, 1),
                            "filename": os.path.basename(output_path),
                        })
        except Exception:
            pass

    pump_thread = threading.Thread(target=_pump_stderr, daemon=True)
    pump_thread.start()

    try:
        process.wait(timeout=300)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("ffmpeg timed out after 300s")

    pump_thread.join(timeout=5)

    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (code {process.returncode})")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("ffmpeg produced empty or missing file")

    file_size = os.path.getsize(output_path)
    log.info(f"Download complete via ffmpeg: {output_path} ({file_size} bytes)")
    if progress_callback:
        progress_callback({
            "status": "done",
            "percent": 100,
            "filename": os.path.basename(output_path),
        })
    return output_path


def _download_httpx(url, output_path, headers, cookies, progress_callback):
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    client = httpx.Client(headers=headers, cookies=cookies or {}, follow_redirects=True, timeout=30, verify=False, limits=limits)

    try:
        total_size = 0
        url_lower = url.split("?")[0].lower()
        is_direct = (
            url_lower.endswith(".mp4")
            or url_lower.endswith(".mkv")
            or url_lower.endswith(".webm")
        )

        if not is_direct:
            try:
                head = client.head(url)
                content_type = head.headers.get("content-type", "")
                total_size = int(head.headers.get("content-length", 0))
                is_direct = "video/mp4" in content_type or "video/webm" in content_type
            except Exception:
                try:
                    resp = client.get(url, headers={**headers, "Range": "bytes=0-0"})
                    content_type = resp.headers.get("content-type", "")
                    total_size = int(resp.headers.get("content-range", "").split("/")[-1]) if "/" in resp.headers.get("content-range", "") else 0
                    is_direct = "video/mp4" in content_type or "video/webm" in content_type or resp.status_code == 206
                except Exception:
                    pass

        if is_direct:
            return _download_direct_file(client, url, output_path, total_size, progress_callback)
        else:
            return _download_hls(client, url, output_path, headers, cookies, progress_callback)
    finally:
        client.close()


def _download_direct_file(client, url, output_path, total_size, progress_callback):
    log.info(f"Direct file download (total_size={total_size} bytes)...")
    downloaded = 0
    CHUNK = 1024 * 1024  # 1MB chunks
    last_log_mb = 0

    with open(output_path, "wb") as f:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            if total_size == 0:
                cl = resp.headers.get("content-length", "")
                if cl.isdigit():
                    total_size = int(cl)

            for chunk in resp.iter_bytes(CHUNK):
                f.write(chunk)
                downloaded += len(chunk)

                if progress_callback and total_size > 0:
                    percent = min(99, (downloaded / total_size) * 100)
                    speed = f"{downloaded // (1024*1024)}/{total_size // (1024*1024)} MB"
                    progress_callback({
                        "status": "downloading",
                        "percent": round(percent, 1),
                        "speed": speed,
                        "filename": os.path.basename(output_path),
                    })

                cur_mb = downloaded // (50 * 1024 * 1024)
                if cur_mb > last_log_mb:
                    last_log_mb = cur_mb
                    if total_size > 0:
                        log.info(f"Downloaded {downloaded // (1024*1024)}/{total_size // (1024*1024)} MB ({downloaded * 100 // total_size}%)")
                    else:
                        log.info(f"Downloaded {downloaded // (1024*1024)} MB")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Downloaded file is empty")
    
    file_size = os.path.getsize(output_path)
    log.info(f"Download complete: {output_path} ({file_size} bytes)")
    if progress_callback:
        progress_callback({
            "status": "done",
            "percent": 100,
            "filename": os.path.basename(output_path),
        })
    return output_path


def _download_hls(client, url, output_path, headers, cookies, progress_callback):
    PARALLEL_WORKERS = 8
    log.info("Starting HLS segment download (parallel)...")
    tmpdir = None
    concat_file = None
    segment_files = []

    try:
        resp = client.get(url)
        resp.raise_for_status()
        master_content = resp.text
        master_url = str(resp.url)

        variant_url = parse_best_variant(master_content, master_url)

        if variant_url == master_url:
            variant_content = master_content
        else:
            log.info(f"Best variant: {variant_url[:120]}...")
            resp2 = client.get(variant_url)
            resp2.raise_for_status()
            variant_content = resp2.text
            variant_url = str(resp2.url)

        segments = parse_segments(variant_content, variant_url)
        log.info(f"Found {len(segments)} segments, downloading with {PARALLEL_WORKERS} workers")

        if not segments:
            raise RuntimeError("No segments found in playlist")

        tmpdir = tempfile.mkdtemp(prefix="yummy_dl_")
        concat_file = os.path.join(tmpdir, "concat.txt")
        segment_files = [None] * len(segments)
        completed = 0

        def download_segment(args):
            i, seg_url = args
            seg_path = os.path.join(tmpdir, f"seg_{i:05d}.ts")
            last_err = None
            for attempt in range(3):
                try:
                    resp = client.get(seg_url)
                    resp.raise_for_status()
                    with open(seg_path, "wb") as f:
                        f.write(resp.content)
                    return i, seg_path
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        import time
                        time.sleep(1 * (attempt + 1))
            raise last_err

        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            futures = {pool.submit(download_segment, (i, url)): i for i, url in enumerate(segments)}
            for future in as_completed(futures):
                i, seg_path = future.result()
                segment_files[i] = seg_path
                completed += 1

                if progress_callback and completed % 10 == 0:
                    percent = min(95, (completed / len(segments)) * 95)
                    progress_callback({
                        "status": "downloading",
                        "percent": round(percent, 1),
                        "filename": os.path.basename(output_path),
                    })

                if completed % 50 == 0:
                    log.info(f"Downloaded {completed}/{len(segments)} segments")

        log.info(f"All {len(segments)} segments downloaded, muxing with ffmpeg...")

        with open(concat_file, "w") as f:
            for sf in segment_files:
                f.write(f"file '{sf}'\n")

        if progress_callback:
            progress_callback({
                "status": "downloading",
                "percent": 96,
                "filename": os.path.basename(output_path),
            })

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed (code {process.returncode}): {stderr[-500:]}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("ffmpeg produced empty file after concat")

        file_size = os.path.getsize(output_path)
        log.info(f"Download complete via httpx: {output_path} ({file_size} bytes)")
        if progress_callback:
            progress_callback({
                "status": "done",
                "percent": 100,
                "filename": os.path.basename(output_path),
            })
        return output_path

    finally:
        for sf in segment_files:
            if sf:
                try:
                    os.remove(sf)
                except OSError:
                    pass
        if concat_file:
            try:
                os.remove(concat_file)
            except OSError:
                pass
        if tmpdir:
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass


def parse_best_variant(master_content, master_url):
    if "#EXT-X-STREAM-INF" not in master_content:
        return master_url

    base_url = master_url.rsplit("/", 1)[0] + "/"
    lines = master_content.strip().splitlines()
    variants = []

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            bw_match = re.search(r"BANDWIDTH=(\d+)", line)
            res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
            bw = int(bw_match.group(1)) if bw_match else 0
            height = int(res_match.group(2)) if res_match else 0
            if i + 1 < len(lines):
                variant_path = lines[i + 1].strip()
                if variant_path.startswith("http"):
                    variant_url = variant_path
                else:
                    variant_url = base_url + variant_path
                variants.append((bw, height, variant_url))

    if not variants:
        return master_url

    variants.sort(key=lambda v: v[1], reverse=True)
    return variants[0][2]


def parse_segments(variant_content, variant_url):
    base_url = variant_url.rsplit("/", 1)[0] + "/"
    segments = []
    for line in variant_content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("http"):
            segments.append(line)
        else:
            segments.append(base_url + line)
    return segments

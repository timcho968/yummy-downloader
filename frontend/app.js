const KODIK_BATCH = 4;
let currentAnime = null;
let allEpisodes = [];
let ws = null;
let lastEpisodeSpeed = 0;

function setStatus(id, msg, type = '') {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.className = 'status ' + type;
}

function show(id) {
    document.getElementById(id).classList.remove('hidden');
}

function hide(id) {
    document.getElementById(id).classList.add('hidden');
}

async function loadSettings() {
    try {
        const resp = await fetch('/api/settings');
        const data = await resp.json();
        document.getElementById('download-dir').value = data.download_dir || '';
        if (typeof data.sibnet_pause === 'number') {
            document.getElementById('sibnet-pause').value = data.sibnet_pause;
        }
    } catch {}
}

async function saveDownloadDir() {
    const dir = document.getElementById('download-dir').value.trim();
    if (!dir) return;
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ download_dir: dir }),
        });
    } catch {}
}

async function savePause() {
    const pause = parseInt(document.getElementById('sibnet-pause').value) || 0;
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sibnet_pause: pause }),
        });
    } catch {}
}

async function loadAnime() {
    const urlInput = document.getElementById('anime-url');
    const url = urlInput.value.trim();
    if (!url) return;

    setStatus('search-status', 'Загрузка...', '');

    // Extract anime URL path from full URL
    let animePath = url;
    try {
        const parsed = new URL(url);
        animePath = parsed.pathname.replace(/^\//, '');
        // Handle different URL formats
        if (animePath.startsWith('anime/')) {
            animePath = animePath.substring(6);
        } else if (animePath.startsWith('catalog/item/')) {
            animePath = animePath.substring(13);
        }
    } catch {
        // Not a full URL, use as-is
    }

    try {
        const resp = await fetch(`/api/anime/${animePath}`);
        const data = await resp.json();

        if (data.error) {
            setStatus('search-status', 'Ошибка: ' + data.error, 'error');
            return;
        }

        currentAnime = data.data;
        allEpisodes = (currentAnime.episodes || []).filter(ep => {
            const p = (ep.player || '').toLowerCase();
            return p.includes('kodik') || p.includes('sibnet');
        });

        // Update UI
        document.getElementById('anime-title').textContent = currentAnime.name;
        document.getElementById('anime-rating').textContent =
            currentAnime.rating ? `Рейтинг: ${currentAnime.rating}` : '';
        document.getElementById('anime-desc').textContent =
            currentAnime.description || '';

        const poster = document.getElementById('anime-poster');
        if (currentAnime.poster) {
            poster.src = currentAnime.poster;
            poster.style.display = 'block';
        } else {
            poster.style.display = 'none';
        }

        populateDubbingSelect();
        filterEpisodes();
        show('anime-info');
        show('episodes-section');
        setStatus('search-status', `Найдено ${allEpisodes.length} серий (Kodik/Sibnet)`, 'success');
    } catch (e) {
        setStatus('search-status', 'Ошибка загрузки: ' + e.message, 'error');
    }
}

function populateDubbingSelect() {
    const select = document.getElementById('dubbing-select');
    const dubbings = [...new Set(allEpisodes.map(e => e.dubbing))];

    select.innerHTML = '';
    dubbings.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d;
        opt.textContent = d;
        select.appendChild(opt);
    });
    populatePlayerSelect();
}

function populatePlayerSelect() {
    const dubbing = document.getElementById('dubbing-select').value;
    const select = document.getElementById('player-select');
    const players = [...new Set(
        allEpisodes.filter(e => e.dubbing === dubbing).map(e => e.player)
    )];

    const prev = select.value;
    select.innerHTML = '';
    players.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p;
        opt.textContent = p;
        select.appendChild(opt);
    });
    if (players.includes(prev)) select.value = prev;
}

function filterEpisodes() {
    const dubbing = document.getElementById('dubbing-select').value;
    const player = document.getElementById('player-select').value;
    const filtered = allEpisodes.filter(e => e.dubbing === dubbing && e.player === player);

    const list = document.getElementById('episodes-list');
    list.innerHTML = '';

    filtered.forEach(ep => {
        const card = document.createElement('div');
        card.className = 'episode-card';
        card.innerHTML = `
            <input type="checkbox" data-video-id="${ep.video_id}" data-iframe="${ep.iframe_url}" data-number="${ep.number}" data-dubbing="${ep.dubbing}" data-player="${ep.player}" />
            <span class="ep-number">#${ep.number}</span>
            <span class="ep-player">${ep.player}</span>
        `;
        card.onclick = (e) => {
            if (e.target.tagName !== 'INPUT') {
                const cb = card.querySelector('input');
                cb.checked = !cb.checked;
            }
            card.classList.toggle('selected', card.querySelector('input').checked);
        };
        list.appendChild(card);
    });

    const pg = document.getElementById('sibnet-pause-group');
    if (pg) pg.style.display = player.toLowerCase().includes('kodik') ? 'none' : '';
}

function selectAll() {
    document.querySelectorAll('.episode-card input').forEach(cb => {
        cb.checked = true;
        cb.closest('.episode-card').classList.add('selected');
    });
}

function deselectAll() {
    document.querySelectorAll('.episode-card input').forEach(cb => {
        cb.checked = false;
        cb.closest('.episode-card').classList.remove('selected');
    });
}

function selectRange() {
    const from = parseInt(document.getElementById('range-from').value) || 0;
    const to = parseInt(document.getElementById('range-to').value) || 0;
    if (!from || !to || from > to) {
        alert('Укажи корректный диапазон (от <= до)');
        return;
    }

    let selected = 0;
    document.querySelectorAll('.episode-card').forEach(card => {
        const cb = card.querySelector('input');
        const num = parseInt(cb.dataset.number);
        if (num >= from && num <= to) {
            cb.checked = true;
            card.classList.add('selected');
            selected++;
        } else {
            cb.checked = false;
            card.classList.remove('selected');
        }
    });
}

async function startBatchDownload() {
    const checked = document.querySelectorAll('.episode-card input:checked');
    if (checked.length === 0) {
        alert('Выбери серии для скачивания');
        return;
    }

    const quality = document.getElementById('quality-select').value;
    const animeName = currentAnime.name.replace(/[<>:"/\\|?*]/g, '_');

    show('progress-section');
    const progressList = document.getElementById('progress-list');
    progressList.innerHTML = '';

    connectWebSocket();

    const episodes = Array.from(checked);

    const player = episodes[0].dataset.player || '';
    const isSibnet = !player.toLowerCase().includes('kodik');
    const batchSize = isSibnet ? 1 : KODIK_BATCH;

    let basePause = parseInt(document.getElementById('sibnet-pause').value) || 30;
    basePause = Math.max(0, Math.min(300, basePause));
    let currentPause = basePause;
    lastEpisodeSpeed = 0;

    for (let i = 0; i < episodes.length; i += batchSize) {
        const batch = episodes.slice(i, i + batchSize);

        for (const cb of batch) {
            const number = cb.dataset.number;
            const item = document.createElement('div');
            item.className = 'progress-item';
            item.id = `progress-${number}`;
            item.innerHTML = `
                <div class="progress-info">
                    <span>EP${number} - Получение ссылки...</span>
                    <span class="progress-speed"></span>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar" style="width: 0%"></div>
                </div>
            `;
            progressList.appendChild(item);
        }

        const batchPromises = batch.map(cb => startEpisode(cb, quality, animeName, player));
        await Promise.all(batchPromises);

        // Пауза между сериями для Sibnet: восстанавливает per-IP квоту скорости.
        // Адаптивно: если прошлая серия шла медленно (<2 МБ/с) — удваиваем паузу.
        if (isSibnet && i + batchSize < episodes.length) {
            if (lastEpisodeSpeed > 0 && lastEpisodeSpeed < 2) {
                currentPause = Math.min(300, currentPause * 2);
                log(`Серия шла медленно (${lastEpisodeSpeed.toFixed(2)} МБ/с) — увеличиваю паузу до ${currentPause}с`);
            }
            await sleepWithCountdown(currentPause);
            currentPause = basePause;
        }
    }
    batchBanner('');
}

function batchBanner(msg) {
    let el = document.getElementById('batch-banner');
    if (!el) {
        el = document.createElement('div');
        el.id = 'batch-banner';
        el.className = 'status';
        const sec = document.getElementById('progress-section');
        if (sec) sec.prepend(el);
    }
    el.textContent = msg;
    el.className = 'status ' + (msg ? 'warning' : '');
}

async function sleepWithCountdown(sec) {
    for (let i = sec; i > 0; i--) {
        batchBanner(`Ожидание антибот: ${i}с (пауза восстанавливает квоту скорости)...`);
        await new Promise(r => setTimeout(r, 1000));
    }
}

function log(msg) {
    // lightweight console logging for adaptive decisions
    if (window.console) console.log('[batch] ' + msg);
}

function startEpisode(cb, quality, animeName, player) {
    return new Promise(async (resolve) => {
        const number = cb.dataset.number;
        const iframeUrl = cb.dataset.iframe;
        const filename = `${animeName}/EP${number}.mp4`;
        const clientId = Date.now() + '-' + Math.random().toString(36).slice(2, 8);

        const donePromise = waitForCompletion(clientId);

        try {
            const player = cb.dataset.player || '';

            const resolveResp = await fetch('/api/resolve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ iframe_url: iframeUrl, player: player }),
            });
            const resolveData = await resolveResp.json();

            if (resolveData.error || !resolveData.data || resolveData.data.length === 0) {
                updateProgress(number, { status: 'error', error: resolveData.error || 'Не удалось получить ссылку' });
                donePromise.cancel();
                resolve();
                return;
            }

            const streams = resolveData.data;
            let stream = streams.find(s => s.quality === quality + 'p');
            if (!stream) stream = streams.find(s => s.quality.includes(quality));
            if (!stream) stream = streams[0];

            updateProgress(number, { status: 'downloading', percent: 0 });

            const dlResp = await fetch('/api/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: stream.url,
                    output_path: filename,
                    quality: quality,
                    extra_headers: stream.headers || {},
                    client_id: clientId,
                    iframe_url: iframeUrl,
                    player: player,
                }),
            });
            const dlData = await dlResp.json();
            if (dlData.error) {
                updateProgress(number, { status: 'error', error: dlData.error });
                donePromise.cancel();
                resolve();
                return;
            }

            await donePromise.promise;
        } catch (e) {
            updateProgress(number, { status: 'error', error: e.message });
        }
        resolve();
    });
}

function waitForCompletion(clientId) {
    let cancelFn;
    const promise = new Promise((resolve) => {
        const handler = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (!data.client_id || data.client_id !== clientId) return;

                if (data.status === 'done' || data.status === 'error') {
                    ws.removeEventListener('message', handler);
                    resolve();
                }
            } catch (e) {}
        };
        ws.addEventListener('message', handler);
        cancelFn = () => { ws.removeEventListener('message', handler); resolve(); };
    });
    return { promise, cancel: cancelFn };
}

function updateProgress(number, info) {
    const item = document.getElementById(`progress-${number}`);
    if (!item) return;

    const bar = item.querySelector('.progress-bar');
    const infoSpan = item.querySelector('.progress-info span:first-child');
    const speedSpan = item.querySelector('.progress-speed');

    if (info.status === 'downloading') {
        bar.style.width = (info.percent || 0) + '%';
        bar.className = 'progress-bar';
        infoSpan.textContent = `EP${number} - Скачивание...`;
        speedSpan.textContent = info.speed || '';
        const m = (info.speed || '').match(/([\d.]+)\s*MB\/s/);
        if (m) lastEpisodeSpeed = parseFloat(m[1]);
    } else if (info.status === 'done') {
        bar.style.width = '100%';
        bar.className = 'progress-bar done';
        infoSpan.textContent = `EP${number} - Готово!`;
        speedSpan.textContent = '';
    } else if (info.status === 'error') {
        bar.style.width = '100%';
        bar.className = 'progress-bar error';
        infoSpan.textContent = `EP${number} - Ошибка: ${info.error}`;
        speedSpan.textContent = '';
    } else if (info.status === 'processing') {
        bar.style.width = '100%';
        bar.className = 'progress-bar';
        infoSpan.textContent = `EP${number} - Обработка...`;
        speedSpan.textContent = '';
    }
}

function connectWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) return;

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws/progress`);

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.download_id) {
                const match = data.filename && data.filename.match(/EP(\d+)/);
                if (match) {
                    updateProgress(match[1], data);
                }
            }
        } catch {}
    };

    ws.onclose = () => {
        setTimeout(connectWebSocket, 3000);
    };

    // Keep alive
    setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
        }
    }, 30000);
}

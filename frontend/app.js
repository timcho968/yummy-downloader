let currentAnime = null;
let allEpisodes = [];
let ws = null;

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
    const dubbing = document.getElementById('dubbing-select').value;

    show('progress-section');
    const progressList = document.getElementById('progress-list');
    progressList.innerHTML = '';

    connectWebSocket();

    for (const cb of checked) {
        const iframeUrl = cb.dataset.iframe;
        const number = cb.dataset.number;
        const filename = `${animeName}/EP${number}.mp4`;

        // Add progress item
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
                continue;
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
                }),
            });
            const dlData = await dlResp.json();
            if (dlData.error) {
                updateProgress(number, { status: 'error', error: dlData.error });
            }
        } catch (e) {
            updateProgress(number, { status: 'error', error: e.message });
        }
    }
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

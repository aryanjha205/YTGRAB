document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('terabox-url');
    const extractBtn = document.getElementById('extract-btn');
    const btnText = extractBtn.querySelector('.btn-text');
    const spinner = extractBtn.querySelector('.spinner');
    const errorToast = document.getElementById('error-message');
    const resultSection = document.getElementById('result-section');
    const themeToggle = document.getElementById('theme-toggle');
    const html = document.documentElement;

    const fileName = document.getElementById('file-name');
    const fileSize = document.getElementById('file-size');
    const fileType = document.getElementById('file-type');
    const fileThumb = document.getElementById('file-thumb');
    const thumbFallback = document.getElementById('file-icon-fallback');
    const downloadLink = document.getElementById('download-link');
    const streamBtn = document.getElementById('stream-btn');
    const videoPlayer = document.getElementById('video-player');

    const savedTheme = localStorage.getItem('theme') || 'dark';
    html.setAttribute('data-theme', savedTheme);

    (async function checkBackend() {
        try {
            const resp = await fetch('/api/ping');
            if (!resp.ok) throw new Error('No response');
            const j = await resp.json();
            if (j.status !== 'ok') throw new Error('Bad status');
        } catch (e) {
            showError('Backend not reachable. Try restarting the server.');
            extractBtn.disabled = true;
        }
    })();

    // Register service worker for PWA
    // Register service worker for PWA
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js')
            .then(reg => console.log('SW Registered!', reg))
            .catch(err => console.log('SW Failed!', err));
    }

    themeToggle.addEventListener('click', () => {
        const currentTheme = html.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
    });

    extractBtn.addEventListener('click', handleExtract);

    async function handleExtract() {
        const url = urlInput.value.trim();

        if (!url) {
            showError("Please paste a YouTube link.");
            return;
        }

        hideError();
        setLoading(true);
        resultSection.classList.add('hidden');

        try {
            const response = await fetch('/api/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || "Failed to download.");
            }

            const mediaList = data.media || [data]; // Support multiple media

            const mediaContainer = document.getElementById('media-container');
            mediaContainer.innerHTML = ''; // Clear previous

            mediaList.forEach((media, index) => {
                const mediaItem = document.createElement('div');
                mediaItem.className = 'media-item';

                const previewDiv = document.createElement('div');
                previewDiv.className = 'file-preview';

                if (media.type === 'video') {
                    const videoEl = document.createElement('video');
                    videoEl.className = 'video-preview';
                    videoEl.poster = media.thumbnail || '';
                    videoEl.controls = false;
                    videoEl.playsInline = true;
                    previewDiv.appendChild(videoEl);

                    const playBtn = document.createElement('button');
                    playBtn.className = 'play-overlay';
                    playBtn.textContent = '▶';
                    playBtn.dataset.stream = media.stream_url;
                    playBtn.dataset.index = index;
                    previewDiv.appendChild(playBtn);
                } else {
                    const imgEl = document.createElement('img');
                    imgEl.src = media.thumbnail || '';
                    imgEl.alt = 'Media Preview';
                    previewDiv.appendChild(imgEl);
                }

                const detailsDiv = document.createElement('div');
                detailsDiv.className = 'file-details';

                const nameEl = document.createElement('h2');
                nameEl.textContent = media.filename || `Instagram Media ${index + 1}`;
                detailsDiv.appendChild(nameEl);

                const metaDiv = document.createElement('div');
                metaDiv.className = 'meta-info';

                const sizeEl = document.createElement('span');
                sizeEl.className = 'badge';
                sizeEl.textContent = media.size || '0 MB';
                metaDiv.appendChild(sizeEl);

                const typeEl = document.createElement('span');
                typeEl.className = 'badge';
                typeEl.textContent = media.type.charAt(0).toUpperCase() + media.type.slice(1);
                metaDiv.appendChild(typeEl);

                detailsDiv.appendChild(metaDiv);

                const actionsDiv = document.createElement('div');
                actionsDiv.style.display = 'flex';
                actionsDiv.style.gap = '12px';
                actionsDiv.style.alignItems = 'center';

                const downloadLink = document.createElement('a');
                downloadLink.href = media.proxy_download || media.dlink || '#';
                downloadLink.className = 'download-btn';
                downloadLink.target = '_blank';
                downloadLink.rel = 'noopener noreferrer';
                downloadLink.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download';
                actionsDiv.appendChild(downloadLink);

                detailsDiv.appendChild(actionsDiv);

                mediaItem.appendChild(previewDiv);
                mediaItem.appendChild(detailsDiv);

                mediaContainer.appendChild(mediaItem);
            });

            resultSection.classList.remove('hidden');
            resultSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

        } catch (err) {
            showError(err.message || "Failed to download. Please check the link and try again.");
        } finally {
            setLoading(false);
        }
    }

    function setLoading(isLoading) {
        if (isLoading) {
            extractBtn.disabled = true;
            btnText.classList.add('hidden');
            spinner.classList.remove('hidden');
        } else {
            extractBtn.disabled = false;
            btnText.classList.remove('hidden');
            spinner.classList.add('hidden');
        }
    }

    function showError(msg) {
        errorToast.textContent = msg;
        errorToast.classList.remove('hidden');
        urlInput.classList.add('shake');
        setTimeout(() => urlInput.classList.remove('shake'), 500);
    }

    function hideError() {
        errorToast.classList.add('hidden');
    }

    urlInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleExtract();
    });

    // Event delegation for play buttons
    document.getElementById('media-container').addEventListener('click', (e) => {
        if (e.target.classList.contains('play-overlay')) {
            const streamUrl = e.target.dataset.stream;
            if (!streamUrl) return;
            const videoEl = e.target.previousElementSibling; // The video element
            videoEl.src = streamUrl;
            videoEl.style.objectFit = 'contain';
            videoEl.play().then(() => {
                if (videoEl.requestFullscreen) {
                    videoEl.requestFullscreen();
                }
            }).catch(() => { });
        }
    });
});

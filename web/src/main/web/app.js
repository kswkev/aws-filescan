'use strict';

// AWS FileScan UI. All requests are same-origin: the dev proxy (devproxy.py) forwards
// /api/* to the filegateway Function URL and /s3/* to S3 in the emulator.
// User-controlled text (file names) is only ever inserted with textContent.

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 60000;
const STATUSES = ['CLEAN', 'QUARANTINE'];
const STATUS_LABEL = { CLEAN: 'Clean', QUARANTINE: 'Quarantined', PENDING: 'Scanning…' };

const $ = (selector, root = document) => root.querySelector(selector);

// ----------------------------------------------------------------------------- helpers

function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === 'className') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
}

function formatBytes(bytes) {
    if (bytes == null || Number.isNaN(Number(bytes))) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = Number(bytes);
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

function formatTime(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function api(method, path, body) {
    const options = { method, cache: 'no-store', headers: {} };
    if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }
    const response = await fetch(`/api${path}`, options);
    let data = null;
    try {
        data = await response.json();
    } catch {
        // non-JSON error body (e.g. proxy/HTML error page)
    }
    if (!response.ok) {
        throw new Error((data && data.message) || `${response.status} ${response.statusText}`);
    }
    return data;
}

// PUT the file to its pre-signed URL. XMLHttpRequest (not fetch) for upload progress.
function putFile(url, file, contentType, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', url);
        // Must match the contentType the URL was signed with, or S3 answers 403.
        xhr.setRequestHeader('Content-Type', contentType);
        xhr.upload.addEventListener('progress', (event) => {
            if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
        });
        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) resolve();
            else if (xhr.status === 403) reject(new Error('upload rejected by S3 (URL expired or content type changed)'));
            else reject(new Error(`upload failed (${xhr.status})`));
        });
        xhr.addEventListener('error', () => reject(new Error('network error during upload')));
        xhr.addEventListener('abort', () => reject(new Error('upload aborted')));
        xhr.send(file);
    });
}

async function pollStatus(fileId) {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
        const result = await api('GET', `/files/${encodeURIComponent(fileId)}`);
        if (result.status !== 'PENDING') return result;
        await sleep(POLL_INTERVAL_MS);
    }
    return null;
}

// Pre-signed links expire after a few minutes, so always ask for a fresh one on click.
async function download(fileId, button) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = 'Preparing…';
    try {
        const result = await api('GET', `/files/${encodeURIComponent(fileId)}`);
        if (!result.downloadUrl) throw new Error('no download link available');
        const link = el('a', { href: result.downloadUrl, download: result.fileName || '' });
        document.body.append(link);
        link.click();
        link.remove();
    } catch (error) {
        alert(`Download failed: ${error.message}`);
    } finally {
        button.disabled = false;
        button.textContent = original;
    }
}

function downloadButton(fileId, status) {
    const label = status === 'QUARANTINE' ? 'Download (quarantined)' : 'Download';
    const button = el('button', {
        type: 'button',
        className: `link-button${status === 'QUARANTINE' ? ' danger' : ''}`,
        text: label,
    });
    button.addEventListener('click', () => download(fileId, button));
    return button;
}

// ----------------------------------------------------------------------------- uploads

function addUploadRow(file) {
    $('#uploads-empty').hidden = true;
    const name = el('span', { className: 'upload-name', text: file.name });
    const size = el('span', { className: 'muted', text: formatBytes(file.size) });
    const status = el('span', { className: 'upload-status', text: 'Waiting…' });
    const progress = el('progress', { max: '100', value: '0' });
    progress.hidden = true;
    const actions = el('span', { className: 'upload-actions' });
    const item = el('li', { className: 'upload', dataset: { state: 'waiting' } },
        el('span', { className: 'upload-main' }, name, size), status, progress, actions);
    $('#uploads').prepend(item);
    return { item, status, progress, actions };
}

function setState(row, state, text) {
    row.item.dataset.state = state;
    row.status.textContent = text;
}

async function uploadOne(file) {
    const row = addUploadRow(file);
    try {
        setState(row, 'requesting', 'Requesting upload URL…');
        const upload = await api('POST', '/uploads', {
            fileName: file.name,
            contentType: file.type || 'application/octet-stream',
        });

        setState(row, 'uploading', 'Uploading… 0%');
        row.progress.hidden = false;
        await putFile(upload.uploadUrl, file, upload.contentType, (pct) => {
            row.progress.value = pct;
            setState(row, 'uploading', `Uploading… ${pct}%`);
        });
        row.progress.hidden = true;

        setState(row, 'scanning', 'Scanning…');
        const result = await pollStatus(upload.fileId);
        if (!result) {
            setState(row, 'timeout', 'Still scanning – it will appear in the lists below shortly.');
            return;
        }
        setState(row, result.status.toLowerCase(), STATUS_LABEL[result.status] || result.status);
        row.actions.append(downloadButton(upload.fileId, result.status));
        refreshLists();
    } catch (error) {
        row.progress.hidden = true;
        setState(row, 'error', `Failed: ${error.message}`);
    }
}

// ----------------------------------------------------------------------------- lists

function renderList(status, files) {
    const body = $(`tbody[data-list="${status}"]`);
    $(`[data-count="${status}"]`).textContent = String(files.length);
    if (!files.length) {
        body.replaceChildren(el('tr', { className: 'empty' }, el('td', { colspan: '4', className: 'muted', text: 'No files yet.' })));
        return;
    }
    body.replaceChildren(...files.map((file) => el('tr', {},
        el('td', { className: 'file-name', text: file.fileName || file.fileId, title: file.fileName || '' }),
        el('td', { text: formatTime(file.scannedAt) }),
        el('td', { className: 'num', text: formatBytes(file.size) }),
        el('td', { className: 'num' }, downloadButton(file.fileId, status)),
    )));
}

let refreshing = null;

function refreshLists() {
    if (refreshing) return refreshing;
    const button = $('#refresh-button');
    const errorBox = $('#lists-error');
    button.disabled = true;
    refreshing = Promise.all(STATUSES.map((status) => api('GET', `/files?status=${status}`)))
        .then((results) => {
            results.forEach((result, i) => renderList(STATUSES[i], result.files || []));
            errorBox.hidden = true;
        })
        .catch((error) => {
            errorBox.textContent = `Could not load files: ${error.message}`;
            errorBox.hidden = false;
        })
        .finally(() => {
            button.disabled = false;
            refreshing = null;
        });
    return refreshing;
}

// ----------------------------------------------------------------------------- wiring

function selectedFilesChanged(files) {
    const count = files.length;
    $('#selection').textContent = count === 0 ? 'No files selected'
        : count === 1 ? files[0].name : `${count} files selected`;
    $('#upload-button').disabled = count === 0;
}

document.addEventListener('DOMContentLoaded', () => {
    const input = $('#file-input');
    const zone = $('#drop-zone');

    input.addEventListener('change', () => selectedFilesChanged(input.files));

    for (const type of ['dragenter', 'dragover']) {
        zone.addEventListener(type, (event) => {
            event.preventDefault();
            zone.classList.add('dragging');
        });
    }
    for (const type of ['dragleave', 'drop']) {
        zone.addEventListener(type, () => zone.classList.remove('dragging'));
    }
    zone.addEventListener('drop', (event) => {
        event.preventDefault();
        if (event.dataTransfer && event.dataTransfer.files.length) {
            input.files = event.dataTransfer.files;
            selectedFilesChanged(input.files);
        }
    });

    $('#upload-form').addEventListener('submit', (event) => {
        event.preventDefault();
        const files = Array.from(input.files);
        input.value = '';
        selectedFilesChanged([]);
        files.forEach((file) => uploadOne(file));
    });

    $('#refresh-button').addEventListener('click', () => refreshLists());
    refreshLists();
});

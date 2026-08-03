const form = document.querySelector('#target-form');
const message = document.querySelector('#form-message');
const targetList = document.querySelector('#targets');
const emptyState = document.querySelector('#empty-state');
const targetCount = document.querySelector('#target-count');
const upCount = document.querySelector('#up-count');
const downCount = document.querySelector('#down-count');
const CLIENT_ID_KEY = 'sbk-dashboard-client-id';

function createClientId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
}

function browserClientId() {
    try {
        let value = window.sessionStorage.getItem(CLIENT_ID_KEY);
        if (!value) {
            value = createClientId();
            window.sessionStorage.setItem(CLIENT_ID_KEY, value);
        }
        return value;
    } catch (_error) {
        return createClientId();
    }
}

const clientId = browserClientId();

function reportActivity(surface) {
    fetch(`/api/activity/${surface}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clientId}),
        keepalive: true
    }).catch(() => {});
}

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function renderTarget(target) {
    const card = element('article', 'target-card');
    const details = element('div');
    details.append(element('h3', null, target.name));
    const endpoint = element('div', 'endpoint', `${target.host}:${target.port}${target.metricsPath}`);
    const status = element('span', `status ${target.status.state}`, target.status.state);
    status.title = target.status.detail || 'Waiting for status';
    endpoint.append(status);
    details.append(endpoint);
    card.append(details);

    const actions = element('div', 'target-actions');
    const dashboard = element('a', 'dashboard-link', 'Open dashboard ↗');
    dashboard.href = target.dashboardUrl;
    dashboard.target = '_blank';
    dashboard.rel = 'noopener';
    dashboard.addEventListener('click', () => reportActivity('grafana'));
    const remove = element('button', 'delete-button', 'Remove');
    remove.type = 'button';
    remove.addEventListener('click', () => deleteTarget(target, remove));
    actions.append(dashboard, remove);
    card.append(actions);
    return card;
}

function updateEndpointSummary(targets) {
    let up = 0;
    let down = 0;
    for (const target of targets) {
        if (target.status.state === 'up') up += 1;
        if (target.status.state === 'down') down += 1;
    }
    targetCount.textContent = targets.length;
    upCount.textContent = up;
    downCount.textContent = down;
}

async function loadTargets() {
    try {
        const response = await fetch('/api/targets', {cache: 'no-store'});
        if (!response.ok) throw new Error('Unable to load endpoints');
        const targets = await response.json();
        targetList.replaceChildren(...targets.map(renderTarget));
        updateEndpointSummary(targets);
        emptyState.hidden = targets.length !== 0;
    } catch (error) {
        message.textContent = error.message;
    }
}

async function deleteTarget(target, button) {
    if (!window.confirm(`Remove ${target.name} and its generated dashboard?`)) return;
    button.disabled = true;
    const response = await fetch(`/api/targets/${encodeURIComponent(target.id)}`, {method: 'DELETE'});
    if (!response.ok) {
        const body = await response.json();
        message.textContent = body.error || 'Unable to remove endpoint';
    }
    await loadTargets();
}

form.addEventListener('submit', async event => {
    event.preventDefault();
    message.textContent = '';
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    const values = new FormData(form);
    const payload = {
        name: values.get('name'),
        host: values.get('host'),
        port: Number(values.get('port')),
        metricsPath: values.get('metricsPath')
    };
    try {
        const response = await fetch('/api/targets', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Unable to register endpoint');
        form.reset();
        message.textContent = 'Endpoint registered. Its dedicated dashboard is ready.';
        await loadTargets();
    } catch (error) {
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

document.querySelector('#refresh').addEventListener('click', loadTargets);
loadTargets();
reportActivity('landing');
window.setInterval(loadTargets, 10000);
window.setInterval(() => reportActivity('landing'), 30000);

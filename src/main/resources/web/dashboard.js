const query = new URLSearchParams(window.location.search);
const targetId = query.get('id');
const charts = new Map();

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function format(value, name) {
    if (!Number.isFinite(value)) return '—';
    const absolute = Math.abs(value);
    let formatted;
    if (absolute >= 1e12) formatted = `${(value / 1e12).toFixed(2)}T`;
    else if (absolute >= 1e9) formatted = `${(value / 1e9).toFixed(2)}B`;
    else if (absolute >= 1e6) formatted = `${(value / 1e6).toFixed(2)}M`;
    else if (absolute >= 1e3) formatted = `${(value / 1e3).toFixed(2)}K`;
    else formatted = value.toLocaleString(undefined, {maximumFractionDigits: 2});
    if (name.includes('MBPerSec')) return `${formatted} MB/s`;
    if (name.includes('RecordsPerSec')) return `${formatted} rec/s`;
    if (name.includes('_ns_') || name.endsWith('_ns')) return `${formatted} ns`;
    if (name.includes('_ms_') || name.endsWith('_ms')) return `${formatted} ms`;
    return formatted;
}

function preferred(series, terms) {
    return series.find(item => terms.some(term => item.name.includes(term)) && Number.isFinite(item.current));
}

function renderSummary(series) {
    const definitions = [
        ['Throughput', ['MBPerSec']],
        ['Operations', ['RecordsPerSec']],
        ['Average latency', ['AvgLatency']],
        ['P99 latency', ['_99_9', '_99']],
        ['Active writers', ['Writers']],
        ['Active readers', ['Readers']]
    ];
    const cards = definitions.map(([label, terms]) => {
        const metric = preferred(series, terms);
        const card = el('article', 'summary-card');
        card.append(el('p', null, label), el('strong', null, metric ? format(metric.current, metric.name) : '—'));
        card.append(el('small', null, metric ? metric.name : 'Awaiting metric'));
        return card;
    });
    document.querySelector('#summary').replaceChildren(...cards);
}

function chartCandidates(series) {
    const signal = /MBPerSec|RecordsPerSec|AvgLatency|MaxLatency|_(99|99_9)$|Writers$|Readers$|Connections$/;
    const selected = series.filter(item => signal.test(item.name) && item.points.length > 0);
    return (selected.length ? selected : series.filter(item => item.points.length > 0)).slice(0, 12);
}

function chartCard(metric) {
    let card = charts.get(metric.key);
    if (!card) {
        card = el('article', 'chart-card');
        const top = el('div', 'chart-top');
        top.append(el('div', null, metric.name), el('strong', 'chart-value'));
        const canvas = document.createElement('canvas');
        canvas.height = 180;
        card.append(top, canvas);
        card.metricCanvas = canvas;
        charts.set(metric.key, card);
    }
    card.querySelector('.chart-value').textContent = format(metric.current, metric.name);
    draw(card.metricCanvas, metric.points);
    return card;
}

function draw(canvas, points) {
    const width = Math.max(280, canvas.clientWidth || 500);
    const height = 180;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio; canvas.height = height * ratio;
    const context = canvas.getContext('2d');
    context.scale(ratio, ratio);
    context.clearRect(0, 0, width, height);
    points = points.filter(point => Number.isFinite(point.value));
    const values = points.map(point => point.value);
    if (values.length < 1) return;
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= Math.abs(min * .05) || 1; max += Math.abs(max * .05) || 1; }
    const pad = 14;
    context.strokeStyle = 'rgba(132,165,198,.12)'; context.lineWidth = 1;
    for (let row = 1; row < 4; row++) { const y = row * height / 4; context.beginPath(); context.moveTo(0,y); context.lineTo(width,y); context.stroke(); }
    const gradient = context.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, 'rgba(46,233,203,.28)'); gradient.addColorStop(1, 'rgba(46,233,203,0)');
    const coordinates = points.map((point, index) => [pad + index * (width - 2 * pad) / Math.max(1, points.length - 1),
        height - pad - (point.value - min) * (height - 2 * pad) / (max - min)]);
    context.beginPath(); coordinates.forEach(([x,y], i) => i ? context.lineTo(x,y) : context.moveTo(x,y));
    context.lineTo(coordinates.at(-1)[0], height); context.lineTo(coordinates[0][0], height); context.closePath(); context.fillStyle = gradient; context.fill();
    context.beginPath(); coordinates.forEach(([x,y], i) => i ? context.lineTo(x,y) : context.moveTo(x,y));
    context.strokeStyle = '#2ee9cb'; context.lineWidth = 2; context.stroke();
}

function renderTable(series) {
    const rows = series.map(metric => {
        const row = document.createElement('tr');
        const labels = Object.entries(metric.labels).map(([key, value]) => `${key}=${value}`).join(', ');
        [metric.name, labels || '—', format(metric.current, metric.name), format(metric.minimum, metric.name),
            format(metric.maximum, metric.name)].forEach(value => row.append(el('td', null, value)));
        return row;
    });
    document.querySelector('#metrics').replaceChildren(...rows);
    document.querySelector('#metric-count').textContent = `${series.length} series`;
}

async function loadTarget() {
    if (!targetId) throw new Error('Missing endpoint identifier');
    const response = await fetch('/api/targets', {cache: 'no-store'});
    if (!response.ok) throw new Error('Unable to load endpoint');
    const target = (await response.json()).find(item => item.id === targetId);
    if (!target) throw new Error('Endpoint not found');
    document.title = `${target.name} · SBK Dashboard`;
    document.querySelector('#kind').textContent = `${target.kind} · DEDICATED ENDPOINT`;
    document.querySelector('#name').textContent = target.name;
    document.querySelector('#address').textContent = `${target.host}:${target.port}${target.metricsPath}`;
}

async function refresh() {
    try {
        const response = await fetch(`/api/targets/${encodeURIComponent(targetId)}/dashboard?points=240`, {cache: 'no-store'});
        if (!response.ok) throw new Error('Unable to retrieve collected metrics');
        const snapshot = await response.json();
        const status = document.querySelector('#status'); status.textContent = snapshot.status.state; status.className = `status ${snapshot.status.state}`;
        document.querySelector('#updated').textContent = snapshot.collectedAt ? `Updated ${new Date(snapshot.collectedAt).toLocaleTimeString()} · ${snapshot.status.detail}` : snapshot.status.detail;
        renderSummary(snapshot.series);
        const cards = chartCandidates(snapshot.series).map(chartCard);
        document.querySelector('#charts').replaceChildren(...cards);
        renderTable(snapshot.series);
        document.querySelector('#dashboard-message').textContent = snapshot.series.length ? '' : 'Waiting for the endpoint to expose its first metric samples…';
    } catch (error) {
        document.querySelector('#dashboard-message').textContent = error.message;
    }
}

loadTarget().then(refresh).catch(error => document.querySelector('#dashboard-message').textContent = error.message);
window.setInterval(refresh, 5000);
window.addEventListener('resize', refresh);

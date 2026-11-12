// Import from / export to Tally data: folder import, live import from a running TallyPrime (read-only),
// replay of a saved capture, and Tally XML export. Imports run as server-side jobs that are polled here.
let exchangePoll = null;

function exchangeEl(id) { return document.getElementById(id); }

function exchangeStatus(text, isError) {
    const el = exchangeEl('exchange-status');
    el.textContent = text;
    el.style.color = isError ? 'var(--accent-danger)' : '';
}

function openTallyExchange() {
    exchangeEl('exchange-result').replaceChildren();
    exchangeStatus('Choose how to bring Tally data in, or export the active company.');
    exchangeEl('tally-exchange-modal').style.display = 'flex';
}

function exchangeLine(parent, text, bold) {
    const p = document.createElement('p');
    p.textContent = text;
    if (bold) p.style.fontWeight = '700';
    parent.appendChild(p);
}

function showExchangeResult(result) {
    const box = exchangeEl('exchange-result');
    box.replaceChildren();
    exchangeLine(box, (result.verification_ok ? 'Imported and verified: ' : 'Imported, but verification FAILED: ') + (result.tally_company || ''), true);
    const counts = Object.entries(result.counts || {}).map(([k, v]) => k.replace(/_/g, ' ') + ' ' + v).join(' · ');
    exchangeLine(box, counts);
    exchangeLine(box, result.checks + ' checks run against the source' + (result.mode === 'live' ? ' (read live from TallyPrime, ' + result.requests + ' read-only requests)' : '') + '.');
    (result.failed_checks || []).forEach(c => exchangeLine(box, 'FAILED: ' + c.check + ' — expected ' + c.expected + ', got ' + c.actual));
    exchangeLine(box, 'Open it from Select Company (' + result.storage_key + ').');
}

async function runExchangeJob(body) {
    clearInterval(exchangePoll);
    exchangeEl('exchange-result').replaceChildren();
    try {
        const started = await storageRequest('import/start', body);
        exchangeStatus('Importing… this can take a few minutes for a large company. You can keep using TallyForge.');
        exchangePoll = setInterval(async () => {
            try {
                const info = await storageRequest('import/status', {job: started.job});
                if (info.state === 'running') { exchangeStatus('Importing… ' + Math.round(info.seconds) + ' s'); return; }
                clearInterval(exchangePoll);
                if (info.state === 'done') {
                    exchangeStatus('Finished in ' + Math.round(info.seconds) + ' s.', !info.result.verification_ok);
                    showExchangeResult(info.result);
                    if (typeof loadCompaniesList === 'function') loadCompaniesList();
                } else {
                    exchangeStatus('Import failed: ' + info.error, true);
                }
            } catch (err) { clearInterval(exchangePoll); exchangeStatus(err.message, true); }
        }, 1500);
    } catch (err) { exchangeStatus(err.message, true); }
}

function importTallyFolder() {
    runExchangeJob({mode: 'files', folder: exchangeEl('exchange-folder').value.trim()});
}

async function listExchangeCompanies() {
    exchangeStatus('Asking TallyPrime which companies are loaded (read-only)…');
    try {
        const result = await companyOperation('/api/tally/bridge/read', {endpoint: exchangeEl('exchange-endpoint').value.trim(), kind: 'companies'});
        const select = exchangeEl('exchange-company');
        select.replaceChildren();
        result.rows.filter(r => r.name).forEach(r => { const o = document.createElement('option'); o.value = o.textContent = r.name; select.appendChild(o); });
        exchangeStatus(select.options.length ? 'Choose the company to import.' : 'No company is loaded in TallyPrime. Open it there first.', !select.options.length);
    } catch (err) { exchangeStatus(err.message, true); }
}

function importTallyLive() {
    if (!exchangeEl('exchange-confirm-live').checked) { exchangeStatus('Tick the box to confirm you saved your work in TallyPrime.', true); return; }
    runExchangeJob({mode: 'live', endpoint: exchangeEl('exchange-endpoint').value.trim(), company: exchangeEl('exchange-company').value,
                    confirm_live: true, capture_dir: exchangeEl('exchange-capture').value.trim() || undefined});
}

function importTallyReplay() {
    runExchangeJob({mode: 'replay', company: exchangeEl('exchange-replay-company').value.trim(), replay_dir: exchangeEl('exchange-replay').value.trim()});
}

async function exportTallyXml() {
    try {
        const out = await storageRequest('export/xml', {path: exchangeEl('exchange-export-path').value.trim(),
                                                      overwrite: exchangeEl('exchange-overwrite').checked});
        exchangeStatus('Saved ' + out.vouchers.toLocaleString() + ' vouchers to ' + out.path + ' (' + Math.round(out.bytes / 1024).toLocaleString() + ' KB).');
    } catch (err) { exchangeStatus(err.message, true); }
}

async function downloadActiveTallyXml() {
    const response = await fetch('/api/tally/xml/export');
    if (!response.ok) { exchangeStatus('Unable to export company data', true); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    const disposition = response.headers.get('Content-Disposition') || '';
    link.href = url; link.download = (/filename="([^"]+)"/.exec(disposition) || [])[1] || 'company.xml';
    link.click(); URL.revokeObjectURL(url);
}

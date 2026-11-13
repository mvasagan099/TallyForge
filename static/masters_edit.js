/* Alter and delete masters from the masters list: click a ledger, stock item or group row to open its editor. */
(function () {
    "use strict";
    const $ = id => document.getElementById(id);
    const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
    const KINDS = {ledgers: "Ledger", stock_items: "Stock Item", groups: "Group"};
    let editing = null;

    const names = list => (list || []).map(x => typeof x === "string" ? x : (x.name || x.symbol || "")).filter(Boolean);
    const field = (id, label, value, type) => `<label class="tp-opt"><span>${esc(label)}</span><input id="me-${id}" class="tally-input" type="${type || "text"}" value="${esc(value)}" style="width:230px;"></label>`;
    const choice = (id, label, value, options) => `<label class="tp-opt"><span>${esc(label)}</span><select id="me-${id}" class="tally-input" style="width:230px;">${
        options.map(o => `<option value="${esc(o[0])}"${String(o[0]) === String(value) ? " selected" : ""}>${esc(o[1])}</option>`).join("")}</select></label>`;
    const yesNo = [["1", "Yes"], ["0", "No"]];

    function form(kind, d) {
        if (kind === "ledgers") {
            return field("new_name", "Name", d.name) + choice("group_name", "Under", d.group_name, names(state.groups).map(n => [n, n]))
                + field("opening_balance", "Opening balance", d.opening_balance, "number") + choice("opening_balance_type", "Dr / Cr", d.opening_balance_type, [["Dr", "Dr"], ["Cr", "Cr"]])
                + field("gstin", "GSTIN / UIN", d.gstin) + field("state", "State", d.state) + field("pan", "PAN", d.pan) + field("pincode", "Pincode", d.pincode)
                + `<label class="tp-opt"><span>Address</span><textarea id="me-address" class="tally-input" rows="2" style="width:230px;">${esc(d.address)}</textarea></label>`
                + field("credit_period_days", "Credit period (days)", d.credit_period_days, "number") + field("credit_limit", "Credit limit", d.credit_limit, "number")
                + choice("maintain_bill_by_bill", "Maintain balances bill-by-bill", d.maintain_bill_by_bill ? "1" : "0", yesNo);
        }
        if (kind === "stock_items") {
            return field("new_name", "Name", d.name) + choice("uom_symbol", "Unit", d.uom_symbol, names(state.uoms).map(n => [n, n]))
                + field("hsn_code", "HSN / SAC code", d.hsn_code) + field("gst_rate", "GST rate %", d.gst_rate, "number")
                + field("opening_qty", "Opening quantity", d.opening_qty, "number") + field("opening_rate", "Opening rate", d.opening_rate, "number")
                + field("reorder_level", "Reorder level", d.reorder_level, "number") + field("part_number", "Part number", d.part_number);
        }
        const locked = d.reserved ? " disabled" : "";
        return `<div style="font-size:0.75rem; color:var(--text-muted);">${d.reserved ? "A TallyPrime reserved group: its name and place cannot change." : ""}</div>`
            + field("new_name", "Name", d.name).replace("<input ", `<input${locked} `)
            + choice("parent", "Under", d.parent, names(state.groups).filter(n => n !== d.name).map(n => [n, n])).replace("<select ", `<select${locked} `)
            + choice("is_revenue", "Nett debit / credit balances are revenue items", d.is_revenue ? "1" : "0", yesNo).replace("<select ", `<select${locked} `)
            + choice("affects_gross", "Affects gross profit", d.affects_gross ? "1" : "0", yesNo).replace("<select ", `<select${locked} `);
    }

    async function open(kind, name) {
        const res = await fetch(`/api/masters/detail?type=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`);
        const d = await res.json();
        if (!res.ok) { alert(d.error || "Could not open this master"); return; }
        editing = {kind, name, detail: d};
        let m = $("tp-master-modal");
        if (!m) {
            m = document.createElement("div");
            m.className = "modal-overlay"; m.id = "tp-master-modal";
            m.onclick = e => { if (e.target === m) closeMasterEditor(); };
            document.body.appendChild(m);
        }
        const locked = kind === "groups" && d.reserved;
        const warning = kind === "groups" ? "" : `<div style="font-size:0.72rem; color:var(--text-muted);">${d.used_in_vouchers
            ? "Used in vouchers: a new name is applied to all of them. " : ""}Changes stay in TallyForge: they do not reach TallyPrime, and importing from TallyPrime again would bring an old name back as a new master.</div>`;
        m.innerHTML = `<div class="modal-dialog tp-dialog" style="width:560px; max-height:92vh; display:flex; flex-direction:column;">
            <div class="tp-dialog-h">${esc(KINDS[kind])} Alteration <span style="cursor:pointer;" onclick="closeMasterEditor()">&#10005;</span></div>
            <div style="padding:12px 16px; display:flex; flex-direction:column; gap:7px; overflow:auto;">${form(kind, d)}${warning}
                <div id="me-message" style="color:var(--accent-danger); font-size:0.8rem; min-height:1.2em;"></div>
                <div style="display:flex; gap:8px; justify-content:space-between;">
                    <button class="btn-tally-primary" style="background:var(--accent-danger);" onclick="deleteMaster()"${locked ? " disabled" : ""}>Delete (Alt+D)</button>
                    <span style="display:flex; gap:8px;"><button class="btn-tally-primary" onclick="saveMasterEditor()"${locked ? " disabled" : ""}>Accept (Ctrl+A)</button>
                    <button class="btn-tally-primary" style="background:var(--surface-hover); color:var(--text-primary);" onclick="closeMasterEditor()">Quit (Esc)</button></span></div>
            </div></div>`;
        m.style.display = "flex";
    }

    function closeMasterEditor() { const m = $("tp-master-modal"); if (m) m.style.display = "none"; editing = null; }

    async function post(path, body) {
        const res = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
        const out = await res.json().catch(() => ({}));
        if (!res.ok) { $("me-message").textContent = out.error || "Could not save"; return null; }
        return out;
    }
    async function done(kind) { closeMasterEditor(); await loadAppState(); await openMasters(kind); }

    async function saveMasterEditor() {
        if (!editing) return;
        const {kind, name, detail} = editing;
        const changes = {};
        document.querySelectorAll("#tp-master-modal [id^='me-']").forEach(el => {
            const key = el.id.slice(3);
            if (key === "message" || el.disabled) return;
            let value = el.value;
            if (el.type === "number") value = value === "" ? 0 : parseFloat(value);
            else if (key === "maintain_bill_by_bill" || key === "is_revenue" || key === "affects_gross") value = parseInt(value);
            const before = key === "new_name" ? detail.name : detail[key];
            if (String(value) !== String(before ?? "")) changes[key] = value;
        });
        if (!Object.keys(changes).length) { closeMasterEditor(); return; }
        if (await post("/api/masters/alter", {type: kind, name, changes})) await done(kind);
    }

    async function deleteMaster() {
        if (!editing || !confirm(`Delete ${editing.name}? This cannot be undone.`)) return;
        if (await post("/api/masters/delete", {type: editing.kind, name: editing.name})) await done(editing.kind);
    }

    window.closeMasterEditor = closeMasterEditor; window.saveMasterEditor = saveMasterEditor; window.deleteMaster = deleteMaster;
    window.openMasterEditor = open;

    function start() {
        const body = $("masters-table-body");
        if (body) {
            body.addEventListener("click", e => {
                const row = e.target.closest("tr");
                const kind = typeof currentMasterType !== "undefined" ? currentMasterType : "";
                if (!row || !KINDS[kind] || !row.children[0]) return;
                const name = row.children[0].textContent.trim();
                if (name && row.children.length > 1) open(kind, name);
            });
            const style = document.createElement("style");
            style.textContent = "#masters-table-body tr:hover { background: var(--surface-hover); cursor: pointer; }";
            document.head.appendChild(style);
        }
        if (typeof window.openMasters === "function") {
            const original = window.openMasters;
            window.openMasters = async function (type) {
                const r = await original.apply(this, arguments);
                const title = $("masters-title");
                if (title && KINDS[type] && !/click a row/.test(title.textContent)) title.textContent += "  (click a row to alter or delete)";
                return r;
            };
        }
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();

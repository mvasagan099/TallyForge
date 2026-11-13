/* TallyPrime-style shell for TallyForge: Gateway of Tally with the same menus, TallyPrime's keyboard shortcuts (voucher keys,
   F10 Other Vouchers, F12 Configure, Alt+F2 / Alt+F3 / Ctrl+F3, Ctrl+Q, Ctrl+E, Alt+M ...), a TallyPrime style navigation path, and
   an F12 Configuration screen whose options really change behaviour. Loaded after tally.js; it wraps a few of its functions. */
(function () {
    "use strict";
    const KEY = "tf_ui_settings";
    const DEFAULTS = {style: "classic", homePanels: true, acceptPrompt: true, invoiceMode: true, buttonBar: true,
        reportCompact: false, reportHideNil: false, showNarration: true};
    let cfg = Object.assign({}, DEFAULTS);
    try { cfg = Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) { /* defaults */ }
    const $ = id => document.getElementById(id);
    // the working period and current date (F2 / Alt+F2): kept for this browser window, like TallyPrime keeps them while a company is open
    let period = {};
    try { period = JSON.parse(sessionStorage.getItem("tf_period") || "{}"); } catch (e) { /* none set */ }
    const savePeriod = () => { try { sessionStorage.setItem("tf_period", JSON.stringify(period)); } catch (e) { /* not stored */ } };
    const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

    // ------------------------------------------------------------------------------------------------ menus
    const run = {
        report: (name, label) => { const f = () => openReport(name, label); f.__name = name; return f; },
        master: type => () => openMasters(type),
        voucher: type => () => openVoucherEntry(type),
    };
    const REGISTERS = [
        {label: "Sales Register", run: run.report("SalesRegister", "Sales Register")},
        {label: "Purchase Register", run: run.report("PurchaseRegister", "Purchase Register")},
        {label: "Payment Register", run: run.report("PaymentRegister", "Payment Register")},
        {label: "Receipt Register", run: run.report("ReceiptRegister", "Receipt Register")},
        {label: "Journal Register", run: run.report("JournalRegister", "Journal Register")},
        {label: "Contra Register", run: run.report("ContraRegister", "Contra Register")},
        {label: "Credit Note Register", run: run.report("CreditNoteRegister", "Credit Note Register")},
        {label: "Debit Note Register", run: run.report("DebitNoteRegister", "Debit Note Register")},
        {label: "Rejections In & Out Register", run: run.report("RejectionsRegister", "Rejections In & Out Register")},
    ];
    const MORE_REPORTS = [
        {label: "Trial Balance", run: run.report("TrialBalance", "Trial Balance")},
        {label: "Day Book", run: run.report("DayBook", "Day Book")},
        {label: "Account Books", title: "Account Books", sub: [
            {label: "Ledger", run: run.report("GeneralLedger", "General Ledger Statement")},
            {label: "Group Summary", run: run.report("GroupSummary", "Group Summary")},
            {label: "Registers", title: "Registers", sub: REGISTERS},
            {label: "Cheque Register", run: run.report("ChequeRegister", "Cheque Register & Books")},
            {label: "Banking (Reconciliation)", run: run.report("BankReconciliation", "Banking & BRS")},
        ]},
        {label: "Statements of Accounts", title: "Statements of Accounts", sub: [
            {label: "Outstandings: Receivables", run: run.report("OutstandingReceivables", "Bills Outstanding (Receivables)")},
            {label: "Outstandings: Payables", run: run.report("OutstandingPayables", "Bills Payable (Ageing)")},
            {label: "Cash Flow", run: run.report("CashFlow", "Cash Flow Statement")},
            {label: "Funds Flow", run: run.report("FundsFlow", "Funds Flow Statement")},
            {label: "Ratio Analysis", run: run.report("RatioAnalysis", "Ratio Analysis")},
            {label: "Budget Variance", run: run.report("BudgetVariance", "Budget Variance Analysis")},
            {label: "Cost Centres", run: run.report("CostCentres", "Cost Centres Breakdown")},
            {label: "Consolidated Group", run: run.report("ConsolidatedFinancials", "Consolidated Group")},
            {label: "Schedule III Balance Sheet", run: run.report("ScheduleIIIBalanceSheet", "Schedule III Balance Sheet")},
        ]},
        {label: "Inventory Books", title: "Inventory Books", sub: [
            {label: "Stock Summary", run: run.report("StockSummary", "Stock Summary")},
            {label: "Purchase Orders (pending)", run: run.report("PurchaseOrders", "Purchase Orders (pending)")},
            {label: "Sales Orders (pending)", run: run.report("SalesOrders", "Sales Orders (pending)")},
            {label: "Order Tracking", run: run.report("OrderTracking", "Sales & Purchase Orders")},
            {label: "Delivery & Receipt Notes", run: run.report("Challans", "Delivery & Receipt Challans")},
            {label: "Inventory Ageing", run: run.report("InventoryAgeing", "Inventory Ageing")},
            {label: "Manufacturing Cost", run: run.report("MfgCost", "Manufacturing Cost")},
            {label: "Landed Cost", run: run.report("LandedCost", "Landed Cost Allocation")},
            {label: "Fixed Assets", run: run.report("FixedAssets", "Fixed Assets & Depreciation")},
            {label: "Price Lists", run: () => openPriceLists()},
        ]},
        {label: "Statutory Reports", title: "Statutory Reports", sub: [
            {label: "GST Summary (by rate)", run: run.report("GSTSummary", "GST Summary")},
            {label: "GSTR-1", run: run.report("GSTR1", "GSTR-1 Outward Return")},
            {label: "GSTR-3B", run: run.report("GSTR3B", "GSTR-3B Monthly Return")},
            {label: "GSTR-9", run: run.report("GSTR9", "GSTR-9 Annual Return")},
            {label: "GSTR-2B Reconciliation", run: run.report("GSTR2BRecon", "GSTR-2B ITC Reconciliation")},
            {label: "TDS Register", run: run.report("TdsRegister", "TDS Withholding Register")},
            {label: "TCS Register", run: run.report("TCSRegister", "TCS Tax Collected at Source")},
            {label: "MSME 45-Day Compliance", run: run.report("MSMECompliance", "MSME 45-Day Compliance")},
            {label: "Payroll Pay Sheet", run: run.report("PayrollSummary", "Payroll Pay Sheet")},
        ]},
        {label: "Exception & Audit Reports", title: "Exception & Audit Reports", sub: [
            {label: "Edit Log (Statutory Audit)", run: run.report("AuditEditLog", "Statutory Audit (Edit Log)")},
            {label: "Audit Trail Differences", run: run.report("AuditDiff", "MCA Diff Viewer (Audit Trail)")},
            {label: "Payment Settlement Reconciliation", run: run.report("PaymentRecon", "Payment Settlement Reconciliation")},
        ]},
        {label: "Other Modules", title: "Other Modules", sub: [
            {label: "Point of Sale", run: run.report("POSBilling", "Point of Sale (POS)")},
            {label: "Job Work", run: run.report("JobWorkRegister", "Job Work & Subcontracting")},
            {label: "Quality Inspection", run: run.report("QualityInspection", "Quality Control & Inspection")},
            {label: "Multi-Currency Rates", run: run.report("CurrencyExchange", "Multi-Currency Rates")},
            {label: "Forex Revaluation", run: run.report("ForexRevaluation", "Forex Revaluation Statement")},
        ]},
    ];
    const VOUCHER_TYPES = [
        {label: "Accounting Vouchers", title: "Accounting Vouchers", header: true},
        {label: "Contra", key: "F4", type: "Contra"}, {label: "Payment", key: "F5", type: "Payment"}, {label: "Receipt", key: "F6", type: "Receipt"},
        {label: "Journal", key: "F7", type: "Journal"}, {label: "Sales", key: "F8", type: "Sales"}, {label: "Purchase", key: "F9", type: "Purchase"},
        {label: "Credit Note", key: "Alt+F6", type: "Credit Note"}, {label: "Debit Note", key: "Alt+F5", type: "Debit Note"},
        {label: "Memorandum", key: "", type: "Memorandum"}, {label: "Reversing Journal", key: "", type: "Reversing Journal"},
        {label: "Inventory Vouchers", header: true},
        {label: "Stock Journal", key: "Alt+F7", type: "Stock Journal"}, {label: "Delivery Note", key: "Alt+F8", type: "Delivery Note"},
        {label: "Receipt Note", key: "Alt+F9", type: "Receipt Note"}, {label: "Rejections In", key: "Ctrl+F6", type: "Rejections In"},
        {label: "Rejections Out", key: "Ctrl+F5", type: "Rejections Out"}, {label: "Physical Stock", key: "Ctrl+F7", type: "Physical Stock"},
        {label: "Manufacturing Journal", key: "", type: "Manufacturing Journal"},
        {label: "Order Vouchers", header: true},
        {label: "Sales Order", key: "Ctrl+F8", type: "Sales Order"}, {label: "Purchase Order", key: "Ctrl+F9", type: "Purchase Order"},
        {label: "Payroll Vouchers", header: true},
        {label: "Payroll", key: "Ctrl+F4", type: "Payroll"}, {label: "Attendance", key: "", type: "Attendance"},
    ];
    const openType = type => { if (type === "Payroll") runPayrollPrompt(); else openVoucherEntry(type); };
    const voucherItems = () => VOUCHER_TYPES.map(v => v.header ? v : {label: v.label, key: v.key, run: () => openType(v.type)});
    const MASTER_LIST = [
        {label: "Ledgers", run: run.master("ledgers")}, {label: "Groups", run: run.master("groups")},
        {label: "Stock Items", run: run.master("stock_items")}, {label: "Units of Measure", run: run.master("units")},
        {label: "Godowns", run: run.master("godowns")}, {label: "Cost Centres", run: run.master("cost_centres")},
        {label: "Employees", run: run.master("employees")}, {label: "Tax Configurations", run: run.master("tax_configs")},
    ];
    const ROOT = [
        {section: "Masters", items: [
            {label: "Create", hot: "C", title: "Create", sub: MASTER_LIST},
            {label: "Alter", hot: "A", title: "Alter", sub: MASTER_LIST},
            {label: "Chart of Accounts", hot: "H", title: "Chart of Accounts", sub: MASTER_LIST},
        ]},
        {section: "Transactions", items: [
            {label: "Vouchers", hot: "V", title: "Vouchers", sub: voucherItems},
            {label: "Day Book", hot: "D", run: run.report("DayBook", "Day Book")},
        ]},
        {section: "Utilities", items: [
            {label: "Banking", hot: "N", run: run.report("BankReconciliation", "Banking & BRS")},
        ]},
        {section: "Reports", items: [
            {label: "Balance Sheet", hot: "B", run: run.report("BalanceSheet", "Balance Sheet")},
            {label: "Profit & Loss A/c", hot: "P", run: run.report("ProfitAndLoss", "Profit & Loss A/c")},
            {label: "Stock Summary", hot: "S", run: run.report("StockSummary", "Stock Summary")},
            {label: "Ratio Analysis", hot: "R", run: run.report("RatioAnalysis", "Ratio Analysis")},
            {label: "Display More Reports", hot: "M", title: "Display More Reports", sub: MORE_REPORTS},
            {label: "Dashboard", hot: "T", run: () => tpOpenDashboard()},
        ]},
        {section: "", items: [{label: "Quit", hot: "Q", run: () => tpQuit()}]},
    ];

    // ------------------------------------------------------------------------------------------------ menu state and rendering
    let stack = [{title: "Gateway of Tally", items: null}];          // root has null items: ROOT is shown with its sections
    let sel = 0;
    let flat = [];

    const resolve = sub => typeof sub === "function" ? sub() : sub;
    function current() { return stack[stack.length - 1]; }

    function render() {
        const box = $("tp-menu");
        if (!box) return;
        const level = current();
        $("tp-menu-title").textContent = level.title;
        flat = [];
        let html = "";
        const used = new Set();
        const hotFor = (label, wanted) => {
            let h = wanted && !used.has(wanted.toUpperCase()) ? wanted.toUpperCase() : "";
            if (!h) for (const ch of label.toUpperCase()) { if (/[A-Z0-9]/.test(ch) && !used.has(ch)) { h = ch; break; } }
            if (h) used.add(h);
            return h;
        };
        const li = (item, idx) => {
            const hot = hotFor(item.label, item.hot);
            item._hot = hot;
            const pos = hot ? item.label.toUpperCase().indexOf(hot) : -1;
            const text = pos < 0 ? esc(item.label) : esc(item.label.slice(0, pos)) + '<u>' + esc(item.label[pos]) + '</u>' + esc(item.label.slice(pos + 1));
            return `<li class="tp-item${idx === sel ? " tp-sel" : ""}" data-i="${idx}"><span>${text}</span>${item.key ? `<em>${esc(item.key)}</em>` : ""}</li>`;
        };
        if (level.items === null) {
            ROOT.forEach(sec => {
                if (sec.section) html += `<li class="tp-section">${esc(sec.section)}</li>`;
                sec.items.forEach(it => { flat.push(it); html += li(it, flat.length - 1); });
            });
        } else {
            resolve(level.items).forEach(it => {
                if (it.header) { html += `<li class="tp-section">${esc(it.label)}</li>`; return; }
                flat.push(it); html += li(it, flat.length - 1);
            });
        }
        box.innerHTML = html;
        box.querySelectorAll(".tp-item").forEach(el => {
            el.onclick = () => { sel = parseInt(el.dataset.i); choose(); };
        });
        updatePath();
    }

    function choose() {
        const it = flat[sel];
        if (!it) return;
        if (it.sub) { stack.push({title: it.title || it.label, items: it.sub}); sel = 0; render(); return; }
        if (it.run) it.run();
    }
    function up() { if (stack.length > 1) { stack.pop(); sel = 0; render(); return true; } return false; }
    function move(d) { if (!flat.length) return; sel = Math.max(0, Math.min(flat.length - 1, sel + d)); render(); const el = document.querySelector(".tp-item.tp-sel"); if (el) el.scrollIntoView({block: "nearest"}); }

    function tpQuit() { if (confirm("Quit TallyForge?")) alert("To close, simply close this browser tab or window."); }
    function tpOpenDashboard() {
        document.body.classList.add("tp-show-dashboard");
        const dash = $("dashboard-screen"); if (dash) dash.style.display = "flex";
        $("tp-home").style.display = "none";
        updatePath();
    }

    // ------------------------------------------------------------------------------------------------ home panels
    function fmtDate(iso) {
        const d = iso ? new Date(iso + "T00:00:00") : new Date();
        return isNaN(d) ? "" : d.toLocaleDateString("en-GB", {day: "numeric", month: "short", year: "numeric"}).replace(/ /g, "-");
    }
    function refreshInfo() {
        const c = (typeof state !== "undefined" && state.company) || {};
        const today = (period.date ? new Date(period.date + "T00:00:00") : new Date()).toLocaleDateString("en-IN", {weekday: "long", day: "numeric", month: "long", year: "numeric"});
        const panel = $("tp-info");
        if (!panel) return;
        panel.style.display = cfg.homePanels ? "" : "none";
        panel.innerHTML = `
            <div class="tp-box"><div class="tp-box-h">Current Period</div><div class="tp-box-b"><strong>${esc(fmtDate(period.from || c.financial_year_from))} to ${esc(fmtDate(period.to || c.financial_year_to))}</strong></div>
                <div class="tp-box-h">Current Date</div><div class="tp-box-b"><strong>${esc(today)}</strong></div></div>
            <div class="tp-box"><div class="tp-box-h tp-two"><span>Name of Company</span><span>Date of Last Entry</span></div>
                <div class="tp-box-b tp-two"><strong>${esc(c.name || "")}</strong><span>${esc(c.last_voucher_date ? fmtDate(c.last_voucher_date) : "-")}</span></div></div>`;
    }

    // ------------------------------------------------------------------------------------------------ navigation path
    const VOUCHER_KIND = {"Stock Journal": "Inventory", "Delivery Note": "Inventory", "Receipt Note": "Inventory", "Rejections In": "Inventory",
        "Rejections Out": "Inventory", "Physical Stock": "Inventory", "Manufacturing Journal": "Inventory", "Sales Order": "Order",
        "Purchase Order": "Order", "Payroll": "Payroll", "Attendance": "Payroll"};
    function reportPath(name, title) {
        const want = String(name || "").toLowerCase();
        const walk = (items, trail) => {
            for (const it of resolve(items) || []) {
                if (it.header) continue;
                if (it.sub) { const r = walk(it.sub, trail.concat(it.title || it.label)); if (r) return r; continue; }
                if (it.run && it.run.__name && it.run.__name.toLowerCase() === want) return trail.concat(it.label);
            }
            return null;
        };
        const hit = walk(MORE_REPORTS, ["Display More Reports"]);
        if (hit) return hit;
        for (const sec of ROOT) for (const it of sec.items) if (it.run && it.run.__name && it.run.__name.toLowerCase() === want) return [it.label];
        return ["Display More Reports", title || name];
    }
    function tpPath() {
        const screen = (typeof state !== "undefined" && state.currentScreen) || "gateway";
        const head = "Gateway of Tally";
        if (screen === "voucher") {
            const type = state.voucherType || "";
            const kind = VOUCHER_KIND[type] || "Accounting";
            const altering = $("vch-id") && $("vch-id").value;
            return [head, "Vouchers", `${kind} Voucher ${altering ? "Alteration" : "Creation"}`, type].join(" > ");
        }
        if (screen === "report") {
            const title = ($("report-title") && $("report-title").textContent) || state.currentReportName;
            return [head].concat(reportPath(state.currentReportName, title)).join(" > ");
        }
        if (screen === "masters") return ($("breadcrumb").textContent || head).replace(/^TallyForge.*/, head);
        if (screen === "tdl") return head + " > TDL Studio";
        const levels = stack.slice(1).map(l => l.title);
        if (document.body.classList.contains("tp-show-dashboard")) return head + " > Dashboard";
        return [head].concat(levels).join(" > ");
    }
    let writing = false;
    let applying = false;
    function updatePath() {
        const el = $("breadcrumb");
        if (!el || !document.body.classList.contains("tp-classic")) return;
        const text = tpPath();
        if (el.textContent !== text) { writing = true; el.textContent = text; writing = false; }
    }

    // ------------------------------------------------------------------------------------------------ screens: home visibility
    function screenOpen() {
        return ["report-screen", "voucher-screen", "masters-screen", "tdl-studio-screen"].some(id => { const e = $(id); return e && e.style.display && e.style.display !== "none"; });
    }
    function syncHome() {
        const home = $("tp-home");
        if (!home) return;
        const classic = cfg.style === "classic";
        const gateway = classic && !screenOpen();
        if (screenOpen()) document.body.classList.remove("tp-show-dashboard");
        home.style.display = gateway && !document.body.classList.contains("tp-show-dashboard") ? "flex" : "none";
        if (classic && !screenOpen() && document.body.classList.contains("tp-show-dashboard")) { const d = $("dashboard-screen"); if (d) d.style.display = "flex"; }
        updatePath();
    }

    // ------------------------------------------------------------------------------------------------ F10 Other Vouchers, F12 Configure
    function openOtherVouchers() {
        let m = $("tp-vouchers-modal");
        if (!m) {
            m = document.createElement("div");
            m.className = "modal-overlay"; m.id = "tp-vouchers-modal";
            m.onclick = e => { if (e.target === m) closeOtherVouchers(); };
            document.body.appendChild(m);
        }
        const rows = VOUCHER_TYPES.map((v, i) => v.header ? `<li class="tp-section">${esc(v.label)}</li>`
            : `<li class="tp-item" data-i="${i}"><span>${esc(v.label)}</span><em>${esc(v.key)}</em></li>`).join("");
        m.innerHTML = `<div class="modal-dialog tp-dialog" style="width:380px; max-height:86vh; display:flex; flex-direction:column;">
            <div class="tp-dialog-h">Change Voucher Type <span style="cursor:pointer;" onclick="closeOtherVouchers()">&#10005;</span></div>
            <ul class="tp-menu" style="overflow:auto;">${rows}</ul></div>`;
        m.querySelectorAll(".tp-item").forEach(el => { el.onclick = () => { closeOtherVouchers(); openType(VOUCHER_TYPES[parseInt(el.dataset.i)].type); }; });
        m.style.display = "flex";
    }
    function closeOtherVouchers() { const m = $("tp-vouchers-modal"); if (m) m.style.display = "none"; }

    function openPeriodDialog() {
        let m = $("tp-period-modal");
        if (!m) {
            m = document.createElement("div");
            m.className = "modal-overlay"; m.id = "tp-period-modal";
            m.onclick = e => { if (e.target === m) closePeriodDialog(); };
            document.body.appendChild(m);
        }
        const c = (typeof state !== "undefined" && state.company) || {};
        const input = (id, label, value) => `<label class="tp-opt"><span>${esc(label)}</span><input id="${id}" type="date" class="tally-input" value="${esc(value || "")}" style="width:190px;"></label>`;
        m.innerHTML = `<div class="modal-dialog tp-dialog" style="width:460px;">
            <div class="tp-dialog-h">Change Period <span style="cursor:pointer;" onclick="closePeriodDialog()">&#10005;</span></div>
            <div style="padding:12px 16px; display:flex; flex-direction:column; gap:8px;">
                ${input("tpp-from", "From", period.from || c.financial_year_from)}
                ${input("tpp-to", "To", period.to || c.financial_year_to)}
                ${input("tpp-date", "Current date (new vouchers)", period.date || "")}
                <div id="tpp-message" style="color:var(--accent-danger); font-size:0.8rem; min-height:1.1em;"></div>
                <div style="display:flex; gap:8px; justify-content:space-between;">
                    <button class="btn-tally-primary" style="background:var(--surface-hover); color:var(--text-primary);" onclick="resetPeriod()">Company year</button>
                    <span style="display:flex; gap:8px;"><button class="btn-tally-primary" onclick="savePeriodDialog()">Accept (Ctrl+A)</button>
                    <button class="btn-tally-primary" style="background:var(--surface-hover); color:var(--text-primary);" onclick="closePeriodDialog()">Quit (Esc)</button></span></div>
                <div style="font-size:0.72rem; color:var(--text-muted);">Reports open for this period. Leave the current date empty to use today's date.</div>
            </div></div>`;
        m.style.display = "flex";
    }
    function closePeriodDialog() { const m = $("tp-period-modal"); if (m) m.style.display = "none"; }
    function applyPeriod() {
        const f = $("report-from-date"), t = $("report-to-date");
        if (f) f.value = period.from || ""; if (t) t.value = period.to || "";
        refreshInfo();
        if (typeof state !== "undefined" && state.currentScreen === "report" && state.currentReportName) {
            const title = ($("report-title") && $("report-title").textContent) || state.currentReportName;
            window.openReport(state.currentReportName, title);
        }
    }
    function savePeriodDialog() {
        const from = $("tpp-from").value, to = $("tpp-to").value, date = $("tpp-date").value;
        if (from && to && from > to) { $("tpp-message").textContent = "The period cannot end before it starts."; return; }
        period = {from, to, date};
        savePeriod(); closePeriodDialog(); applyPeriod();
    }
    function resetPeriod() { period = {}; savePeriod(); closePeriodDialog(); applyPeriod(); }
    window.openPeriodDialog = openPeriodDialog; window.closePeriodDialog = closePeriodDialog;
    window.savePeriodDialog = savePeriodDialog; window.resetPeriod = resetPeriod;
    window.openDatePrompt = openPeriodDialog;                // the F2 button and the old handler now open the period dialog
    window.tpPeriod = () => Object.assign({}, period);

    function openConfig() {
        let m = $("tp-config-modal");
        if (!m) {
            m = document.createElement("div");
            m.className = "modal-overlay"; m.id = "tp-config-modal";
            m.onclick = e => { if (e.target === m) closeConfig(); };
            document.body.appendChild(m);
        }
        const screen = (typeof state !== "undefined" && state.currentScreen) || "gateway";
        const yn = (id, label, value) => `<label class="tp-opt"><span>${esc(label)}</span><select id="${id}" class="tally-input"><option value="1"${value ? " selected" : ""}>Yes</option><option value="0"${value ? "" : " selected"}>No</option></select></label>`;
        const general = `<div class="tp-section" style="padding-left:0;">General</div>
            <label class="tp-opt"><span>Interface style</span><select id="tpc-style" class="tally-input">
                <option value="classic"${cfg.style === "classic" ? " selected" : ""}>Classic (TallyPrime)</option>
                <option value="modern"${cfg.style === "modern" ? " selected" : ""}>Modern dashboard</option></select></label>
            ${yn("tpc-panels", "Show period, date and company panels on the Gateway of Tally", cfg.homePanels)}
            ${yn("tpc-buttons", "Show the right-hand button bar (function keys)", cfg.buttonBar)}`;
        const vouchers = `<div class="tp-section" style="padding-left:0;">Voucher entry</div>
            ${yn("tpc-invoice", "Use invoice mode for Sales, Purchase, Credit Note and Debit Note", cfg.invoiceMode)}
            ${yn("tpc-accept", "Ask \"Accept?\" before saving a voucher", cfg.acceptPrompt)}
            ${yn("tpc-narration", "Show the narration field", cfg.showNarration)}`;
        const reports = `<div class="tp-section" style="padding-left:0;">Reports</div>
            ${yn("tpc-compact", "Compact rows (more lines on the screen)", cfg.reportCompact)}
            ${yn("tpc-hidenil", "Hide lines with nil amounts", cfg.reportHideNil)}`;
        const where = screen === "report" ? "this report" : screen === "voucher" ? "voucher entry" : "the Gateway of Tally";
        m.innerHTML = `<div class="modal-dialog tp-dialog" style="width:560px; max-height:92vh; display:flex; flex-direction:column;">
            <div class="tp-dialog-h">Configuration: ${esc(where)} <span style="cursor:pointer;" onclick="closeConfig()">&#10005;</span></div>
            <div style="padding:12px 16px; display:flex; flex-direction:column; gap:8px; overflow:auto;">
                ${screen === "report" ? reports + general : screen === "voucher" ? vouchers + general : general + vouchers + reports}
                <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:6px;">
                    <button class="btn-tally-primary" onclick="saveConfig()">Accept (Ctrl+A)</button>
                    <button class="btn-tally-primary" style="background:var(--surface-hover); color:var(--text-primary);" onclick="closeConfig()">Quit (Esc)</button></div>
                <div style="font-size:0.72rem; color:var(--text-muted);">Saved in this browser. Options for other screens are in F12 on those screens.</div>
            </div></div>`;
        m.style.display = "flex";
    }
    function closeConfig() { const m = $("tp-config-modal"); if (m) m.style.display = "none"; }
    function saveConfig() {
        const yes = id => { const el = $(id); return el ? el.value === "1" : undefined; };
        if ($("tpc-style")) cfg.style = $("tpc-style").value;
        const set = (key, id) => { const v = yes(id); if (v !== undefined) cfg[key] = v; };
        set("homePanels", "tpc-panels"); set("buttonBar", "tpc-buttons"); set("invoiceMode", "tpc-invoice"); set("acceptPrompt", "tpc-accept");
        set("showNarration", "tpc-narration"); set("reportCompact", "tpc-compact"); set("reportHideNil", "tpc-hidenil");
        try { localStorage.setItem(KEY, JSON.stringify(cfg)); } catch (e) { /* not stored */ }
        closeConfig();
        applyStyle();
    }
    window.openOtherVouchers = openOtherVouchers; window.closeOtherVouchers = closeOtherVouchers;
    window.openConfig = openConfig; window.closeConfig = closeConfig; window.saveConfig = saveConfig;
    window.tpSettings = () => Object.assign({}, cfg);

    function applyStyle() {
        const classic = cfg.style === "classic";
        document.body.classList.toggle("tp-classic", classic);
        document.body.classList.toggle("tp-no-buttonbar", !cfg.buttonBar);
        if (classic) document.documentElement.setAttribute("data-theme", "classic");
        else if (document.documentElement.getAttribute("data-theme") === "classic") {
            let saved = "dark"; try { saved = localStorage.getItem("tf_theme") || "dark"; } catch (e) { /* default */ }
            document.documentElement.setAttribute("data-theme", saved === "classic" ? "dark" : saved);
        }
        if (!classic) { document.body.classList.remove("tp-show-dashboard"); const d = $("dashboard-screen"); if (d && !screenOpen()) d.style.display = "flex"; }
        document.body.classList.toggle("tp-compact-rows", !!cfg.reportCompact);
        ["inv-narration", "vch-narration", "vch-ml-narration"].forEach(id => {
            const el = $(id); if (!el) return;
            const holder = el.closest("label") || el.parentElement;
            if (holder) holder.style.display = cfg.showNarration ? "" : "none";
        });
        applyReportOptions();
        refreshInfo(); syncHome();
    }

    // ------------------------------------------------------------------------------------------------ reports: row cursor, find, nil lines
    let cursor = -1;
    let finding = "";
    const reportRows = () => [...document.querySelectorAll("#report-table-body tr")].filter(r => !r.classList.contains("total-row") && r.querySelector("td") && r.style.display !== "none");
    const isNil = row => {
        const cells = [...row.querySelectorAll("td")].slice(1);
        const nums = cells.map(c => c.textContent.trim()).filter(t => /^[-+₹\s\d.,()DrCr]*$/.test(t) && t !== "" || t === "-");
        return nums.length > 0 && nums.every(t => t === "-" || !/[1-9]/.test(t));
    };
    function applyReportOptions() {
        const rows = document.querySelectorAll("#report-table-body tr");
        const needle = finding.toLowerCase();
        rows.forEach(r => {
            if (r.classList.contains("total-row") || !r.querySelector("td")) { r.style.display = ""; return; }
            const hide = (cfg.reportHideNil && isNil(r)) || (needle && !r.textContent.toLowerCase().includes(needle));
            r.style.display = hide ? "none" : "";
        });
        cursor = -1;
        document.querySelectorAll("#report-table-body tr.tp-cursor").forEach(r => r.classList.remove("tp-cursor"));
    }
    function moveCursor(delta, absolute) {
        const rows = reportRows();
        if (!rows.length) return;
        cursor = absolute !== undefined ? (absolute < 0 ? rows.length - 1 : absolute) : Math.max(0, Math.min(rows.length - 1, cursor + delta));
        document.querySelectorAll("#report-table-body tr.tp-cursor").forEach(r => r.classList.remove("tp-cursor"));
        rows[cursor].classList.add("tp-cursor");
        rows[cursor].scrollIntoView({block: "nearest"});
    }
    function openFindBar() {
        let bar = $("tp-find");
        if (!bar) {
            const host = $("report-table-head") ? $("report-table-head").closest("table") : null;
            if (!host) return;
            bar = document.createElement("div");
            bar.id = "tp-find"; bar.className = "tp-find";
            bar.innerHTML = '<span>Find</span><input id="tp-find-input" class="tally-input" placeholder="type to narrow the lines (Esc clears)">';
            host.parentNode.insertBefore(bar, host);
            $("tp-find-input").addEventListener("input", e => { finding = e.target.value; applyReportOptions(); });
            $("tp-find-input").addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); closeFindBar(); } });
        }
        bar.style.display = "flex"; $("tp-find-input").focus(); $("tp-find-input").select();
    }
    function closeFindBar() { const bar = $("tp-find"); if (bar) bar.style.display = "none"; finding = ""; if ($("tp-find-input")) $("tp-find-input").value = ""; applyReportOptions(); }

    // + and - move the report to the next or previous period: a whole number of months when the period is made of whole months,
    // otherwise the same number of days
    function shiftPeriod(direction) {
        const c = (typeof state !== "undefined" && state.company) || {};
        const from = ($("report-from-date") && $("report-from-date").value) || period.from || c.financial_year_from;
        const to = ($("report-to-date") && $("report-to-date").value) || period.to || c.financial_year_to;
        if (!from || !to) return;
        const a = new Date(from + "T00:00:00"), b = new Date(to + "T00:00:00");
        const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        const wholeMonths = a.getDate() === 1 && new Date(b.getFullYear(), b.getMonth() + 1, 0).getDate() === b.getDate();
        let nf, nt;
        if (wholeMonths) {
            const months = (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth() + 1;
            nf = new Date(a.getFullYear(), a.getMonth() + direction * months, 1);
            nt = new Date(b.getFullYear(), b.getMonth() + 1 + direction * months, 0);
        } else {
            const days = Math.round((b - a) / 86400000) + 1;
            nf = new Date(a.getTime() + direction * days * 86400000); nt = new Date(b.getTime() + direction * days * 86400000);
        }
        period = Object.assign({}, period, {from: iso(nf), to: iso(nt)});
        savePeriod(); applyPeriod();
    }

    // ------------------------------------------------------------------------------------------------ keyboard
    const MODALS = ["command-palette-modal", "goto-modal", "data-modal", "export-modal", "print-modal", "exchange-modal", "import-modal",
        "features-modal", "help-modal", "auth-modal", "tp-config-modal", "tp-vouchers-modal", "pricelist-modal", "tp-master-modal", "tp-period-modal"];
    const anyModal = () => MODALS.some(id => { const e = $(id); return e && e.style.display === "flex"; });
    const companyOpen = () => { const e = $("company-modal"); return e && e.style.display === "flex"; };
    const VOUCHER_KEYS = {"Alt+F7": "Stock Journal", "Alt+F8": "Delivery Note", "Alt+F9": "Receipt Note", "Ctrl+F5": "Rejections Out",
        "Ctrl+F6": "Rejections In", "Ctrl+F7": "Physical Stock", "Ctrl+F8": "Sales Order", "Ctrl+F9": "Purchase Order"};

    async function cancelOpenVoucher() {
        const id = $("vch-id") && $("vch-id").value;
        if (!id) return;
        const reason = prompt("Reason for cancelling this voucher:", "Cancelled by user");
        if (reason === null) return;
        const res = await fetch("/api/vouchers/cancel", {method: "POST", headers: Object.assign({"Content-Type": "application/json"}, typeof authHeaders === "function" ? authHeaders() : {}), body: JSON.stringify({voucher_id: parseInt(id), reason})});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(data.error || "Could not cancel the voucher"); return; }
        await loadAppState(); goBackToGateway();
    }
    async function deleteOpenVoucher() {
        const id = $("vch-id") && $("vch-id").value;
        if (!id || !confirm("Delete this voucher permanently? (Alt+X cancels it and keeps it on record.)")) return;
        const res = await fetch("/api/vouchers/delete", {method: "POST", headers: Object.assign({"Content-Type": "application/json"}, typeof authHeaders === "function" ? authHeaders() : {}), body: JSON.stringify({voucher_id: parseInt(id)})});
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(data.error || "Could not delete the voucher"); return; }
        await loadAppState(); goBackToGateway();
    }
    function removeFocusedLine() {
        if (state.voucherMode !== "invoice") return false;
        const row = document.activeElement && document.activeElement.closest && document.activeElement.closest("#inv-items-body tr");
        if (!row) return false;
        invoiceRemoveRow([...row.parentNode.children].indexOf(row));
        return true;
    }

    function onKey(e) {
        if (companyOpen() || typeof companyReady === "undefined" || !companyReady) return;
        const k = e.key;
        const combo = (e.ctrlKey ? "Ctrl+" : "") + (e.altKey ? "Alt+" : "") + (e.shiftKey && k.length > 1 ? "Shift+" : "") + (k.length === 1 ? k.toUpperCase() : k);
        const take = fn => { e.preventDefault(); e.stopImmediatePropagation(); fn(); };
        const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((document.activeElement || {}).tagName);

        // closing the dialogs of this file
        if (k === "Escape") {
            if ($("tp-config-modal") && $("tp-config-modal").style.display === "flex") return take(closeConfig);
            if ($("tp-vouchers-modal") && $("tp-vouchers-modal").style.display === "flex") return take(closeOtherVouchers);
            if ($("pricelist-modal") && $("pricelist-modal").style.display === "flex") return take(closePriceLists);
            if ($("tp-master-modal") && $("tp-master-modal").style.display === "flex") return take(closeMasterEditor);
            if ($("tp-period-modal") && $("tp-period-modal").style.display === "flex") return take(closePeriodDialog);
        }
        if (combo === "Ctrl+A" && $("tp-period-modal") && $("tp-period-modal").style.display === "flex") return take(savePeriodDialog);
        if ($("tp-master-modal") && $("tp-master-modal").style.display === "flex") {
            if (combo === "Ctrl+A") return take(saveMasterEditor);
            if (combo === "Alt+D") return take(deleteMaster);
        }
        if (combo === "Ctrl+A" && $("tp-config-modal") && $("tp-config-modal").style.display === "flex") return take(saveConfig);

        if (combo === "F10") return take(openOtherVouchers);
        if (combo === "F12") return take(openConfig);
        if (combo === "Alt+F2") return take(openPeriodDialog);
        if (combo === "F2") return take(() => {
            const onVoucher = typeof state !== "undefined" && state.currentScreen === "voucher" && $("vch-date");
            if (onVoucher) { $("vch-date").focus(); if ($("vch-date").select) $("vch-date").select(); } else openPeriodDialog();
        });
        if (combo === "Alt+F3") return take(() => openCompanyModal());
        if (combo === "Ctrl+F3") return take(() => shutActiveCompany());
        if (combo === "Ctrl+F1") return take(() => openHelpModal());
        if (combo === "Ctrl+E" || combo === "Alt+M") return take(() => openExportModal());
        if (VOUCHER_KEYS[combo]) return take(() => openType(VOUCHER_KEYS[combo]));
        if (combo === "Ctrl+Q") return take(() => {
            const open = $("report-screen").style.display !== "none" || $("voucher-screen").style.display !== "none" || $("masters-screen").style.display !== "none";
            if (anyModal()) window.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape"}));
            else if (open || stack.length > 1 || document.body.classList.contains("tp-show-dashboard")) { if (!open && stack.length > 1) up(); else goBackToGateway(); }
            else tpQuit();
        });

        if (anyModal()) return;
        const screen = state.currentScreen;
        if (screen === "voucher") {
            if (combo === "Alt+X") return take(cancelOpenVoucher);
            if (combo === "Alt+D") return take(deleteOpenVoucher);
            if (combo === "Ctrl+H") return take(() => { if ($("vch-mode-toggle-btn") && $("vch-mode-toggle-btn").style.display !== "none") toggleVoucherMode(); });
            if (combo === "Ctrl+D") return take(() => { removeFocusedLine(); });
        }

        if (screen === "report" && !anyModal()) {
            if (combo === "Alt+F" || combo === "Ctrl+F") return take(openFindBar);
            if (!typing) {
                if (k === "ArrowDown") return take(() => moveCursor(1));
                if (k === "ArrowUp") return take(() => moveCursor(-1));
                if (k === "PageDown") return take(() => moveCursor(10));
                if (k === "PageUp") return take(() => moveCursor(-10));
                if (k === "Home") return take(() => moveCursor(0, 0));
                if (k === "End") return take(() => moveCursor(0, -1));
                if (k === "Enter") { const row = reportRows()[cursor]; if (row && row.onclick) return take(() => row.click()); }
                if ((k === "+" || k === "=") && !e.ctrlKey && !e.altKey) return take(() => shiftPeriod(1));
                if ((k === "-" || k === "_") && !e.ctrlKey && !e.altKey) return take(() => shiftPeriod(-1));
            }
        }

        // the Gateway of Tally menu
        const home = $("tp-home");
        if (home && home.style.display === "flex" && !typing) {
            if (k === "ArrowUp") return take(() => move(-1));
            if (k === "ArrowDown") return take(() => move(1));
            if (k === "Home") return take(() => { sel = 0; render(); });
            if (k === "End") return take(() => { sel = flat.length - 1; render(); });
            if (k === "Enter") return take(choose);
            if (k === "Escape") return take(() => { up(); });
            if (k.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
                const i = flat.findIndex(it => it._hot === k.toUpperCase());
                if (i >= 0) return take(() => { sel = i; choose(); });
                return take(() => {});
            }
        } else if (screen === "gateway" && document.body.classList.contains("tp-show-dashboard") && k === "Escape") {
            return take(() => { document.body.classList.remove("tp-show-dashboard"); const d = $("dashboard-screen"); if (d) d.style.display = "none"; syncHome(); });
        }
    }

    // ------------------------------------------------------------------------------------------------ wrappers on tally.js
    function wrap() {
        if (typeof window.openVoucherEntry === "function") {
            const original = window.openVoucherEntry;
            window.openVoucherEntry = function (type, standardOnly) {
                if (!cfg.invoiceMode && standardOnly === undefined && ["Sales", "Purchase", "Credit Note", "Debit Note"].includes(type)) standardOnly = true;
                const result = original.call(this, type, standardOnly);
                if (period.date && $("vch-date")) $("vch-date").value = period.date;      // the current date set with F2
                return result;
            };
        }
        if (typeof window.openReport === "function") {
            const originalReport = window.openReport;
            window.openReport = function (name, title, from, to) {
                if (period.from || period.to) {
                    const f = $("report-from-date"), t = $("report-to-date");
                    if (f && period.from && from === undefined) f.value = period.from;
                    if (t && period.to && to === undefined) t.value = period.to;
                }
                return originalReport.apply(this, arguments);
            };
        }
        let inSave = false;
        const askFirst = (name, nested) => {
            const original = window[name];
            if (typeof original !== "function") return;
            window[name] = function (...args) {
                if (cfg.acceptPrompt && !inSave && !confirm("Accept?")) return undefined;
                const was = inSave;
                if (nested) inSave = true;
                try { return original.apply(this, args); } finally { inSave = was; }
            };
        };
        askFirst("saveVoucher", true);       // saveVoucher may hand over to invoiceSave: ask only once
        askFirst("invoiceSave", false);
        if (typeof window.goBackToGateway === "function") {
            const original = window.goBackToGateway;
            window.goBackToGateway = function () {
                document.body.classList.remove("tp-show-dashboard");
                const r = original.apply(this, arguments);
                if (cfg.style === "classic") { const d = $("dashboard-screen"); if (d) d.style.display = "none"; }
                sel = 0; render(); refreshInfo(); syncHome();
                return r;
            };
        }
    }

    function build() {
        const row = document.querySelector(".tf-body-row");
        const center = $("center-workplace");
        if (!center || $("tp-home")) return;
        const home = document.createElement("div");
        home.id = "tp-home"; home.className = "tp-home"; home.style.display = "none";
        home.innerHTML = `<div class="tp-info" id="tp-info"></div>
            <div class="tp-gateway"><div class="tp-gw-h" id="tp-menu-title">Gateway of Tally</div><ul class="tp-menu" id="tp-menu"></ul></div>`;
        center.insertBefore(home, center.firstChild);
        const bar = document.querySelector(".tally-top-menu");
        if (bar && !bar.querySelector("[data-tp-share]")) {
            const share = document.createElement("button");
            share.className = "top-menu-btn"; share.setAttribute("data-tp-share", "1");
            share.innerHTML = '<span class="underline">M</span>: Share';
            share.onclick = () => openExportModal();
            const printBtn = [...bar.children].find(b => /Print/.test(b.textContent));
            bar.insertBefore(share, printBtn || null);
        }
        void row;
        new MutationObserver(syncHome).observe(center, {attributes: true, attributeFilter: ["style"], subtree: true});
        const crumb = $("breadcrumb");
        if (crumb) new MutationObserver(() => { if (!writing) updatePath(); }).observe(crumb, {childList: true, characterData: true, subtree: true});
        const reportBody = $("report-table-body");
        if (reportBody) new MutationObserver(() => { if (!applying) { applying = true; applyReportOptions(); applying = false; } }).observe(reportBody, {childList: true});
        const name = $("side-company-name");
        if (name) new MutationObserver(refreshInfo).observe(name, {childList: true, characterData: true, subtree: true});
    }

    function start() {
        build(); wrap(); applyStyle(); render();
        window.addEventListener("keydown", onKey, true);
        setInterval(() => { if (cfg.style === "classic" && !screenOpen()) refreshInfo(); }, 5000);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();

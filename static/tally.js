/**
 * TallyForge — Reverse Engineered. Rebuilt. Reimagined.
 * An independently rebuilt accounting and business management application inspired by TallyPrime's functionality.
 * Full module integration:
 * - Accounting, Inventory, Godowns & Manufacturing (BOM)
 * - GST Management & Official GSTR-1 JSON Return Export
 * - Banking & BRS Reconciliation (Manual & CSV statement upload)
 * - Payroll Processing & Monthly Pay Sheets
 * - Statutory Audit Trail (Edit Log) & Local Backups / Restore
 * - Alt+G Go To Smart Search & Keyboard Hotkeys
 * - General Ledger Statement & Cash Flow Direct Method
 * - TDL Studio with live syntax validation (line diagnostics)
 */

let state = {
    currentScreen: "gateway", // "gateway", "report", "voucher", "masters", "tdl_studio"
    company: null,
    ledgers: [],
    stockItems: [],
    godowns: [],
    uoms: [],
    groups: [],
    employees: [],
    costCentres: [],
    selectedMenuIndex: 0,
    gatewayItems: [],
    voucherType: "Payment",
    currentReportName: "",
    selectedLedgerName: "Customer ABC Pvt Ltd",
    calcHistory: []
};

// Non-blocking alert handler for automated and production UI environments
const _nativeAlert = window.alert;
window.alert = function(msg) {
    if (typeof showTallyToast === "function") {
        showTallyToast(String(msg), String(msg).startsWith("✓") ? "success" : "info");
    } else {
        console.log("[TALLY ALERT]", msg);
    }
};

let pendingCompanyKey = null;
let companyReady = false;
let companyContext = sessionStorage.getItem('tf_company_context');
const companyFetch = window.fetch.bind(window);
window.fetch = async function(input, options = {}) {
    if (typeof input === 'string' && input.startsWith('/api/')) {
        const headers = new Headers(options.headers || {});
        if (companyContext) headers.set('X-Company-Context', companyContext);
        const token = sessionStorage.getItem('tally_session_token');
        if (token && !input.endsWith('/storage/select')) headers.set('Authorization', 'Bearer ' + token);
        options = {...options, headers};
    }
    const response = await companyFetch(input, options);
    if (response.status === 409) {
        companyReady = false;
        companyContext = null;
        sessionStorage.removeItem('tf_company_context');
        sessionStorage.removeItem('tally_session_token');
        openCompanyModal();
        throw new Error('Company session closed. Select it again before continuing.');
    }
    if (response.status === 401 && !String(input).includes('/storage/select') && !String(input).includes('/auth/login')) openAuthModal();
    return response;
};

document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    setupKeyboardListeners();
    initGatewayItems();
    companyContext = null;
    sessionStorage.removeItem('tf_company_context');
    sessionStorage.removeItem('tally_session_token');
    openCompanyModal();
});

// -------------------------------------------------------------------------
// Theme Engine & Sidebar Utilities
// -------------------------------------------------------------------------
function initTheme() {
    const saved = localStorage.getItem("tf_theme") || "dark";
    setTheme(saved);

    const savedSidebar = localStorage.getItem("tf_sidebar_collapsed");
    if (savedSidebar === "1") {
        const sidebar = document.getElementById("app-sidebar");
        if (sidebar) sidebar.classList.add("collapsed");
        const icon = document.getElementById("collapse-icon");
        const text = document.getElementById("collapse-text");
        if (icon) icon.textContent = "▶";
        if (text) text.style.display = "none";
    }
}

function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("tf_theme", theme);
    const icon = document.getElementById("theme-toggle-icon");
    if (icon) {
        icon.textContent = (theme === "dark") ? "🌙" : "☀️";
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = (current === "dark") ? "light" : "dark";
    setTheme(next);
}

function toggleSidebarCollapse() {
    const sidebar = document.getElementById("app-sidebar");
    if (!sidebar) return;
    sidebar.classList.toggle("collapsed");
    const isCollapsed = sidebar.classList.contains("collapsed");
    localStorage.setItem("tf_sidebar_collapsed", isCollapsed ? "1" : "0");
    const icon = document.getElementById("collapse-icon");
    const text = document.getElementById("collapse-text");
    if (icon) icon.textContent = isCollapsed ? "▶" : "◀";
    if (text) text.style.display = isCollapsed ? "none" : "inline";
}

async function loadAppState() {
    try {
        const res = await fetch("/api/state");
        if (!res.ok) throw new Error("Unable to load company");
        const data = await res.json();
        companyContext = data.company_context;
        state.company = data.company || {};
        state.ledgers = data.ledgers || [];
        state.stockItems = data.stock_items || [];
        state.godowns = data.godowns || [];
        state.uoms = data.uoms || [];
        state.groups = data.groups || [];
        state.employees = data.employees || [];
        state.costCentres = data.cost_centres || [];

        // Update UI Info Panes
        const compEl = document.getElementById("side-company-name");
        if (compEl) compEl.textContent = state.company.name || "Demo Enterprise Ltd.";
        const gstinEl = document.getElementById("side-company-gstin");
        if (gstinEl) gstinEl.textContent = `GSTIN: ${state.company.gstin || '29AABCT1332L1Z5'}`;
        const lastVchEl = document.getElementById("last-voucher-date-val");
        if (lastVchEl) lastVchEl.textContent = state.company.last_voucher_date || "No vouchers entered";
        const ledgersCountEl = document.getElementById("metrics-ledgers-count");
        if (ledgersCountEl) ledgersCountEl.textContent = `${state.ledgers.length} Ledgers`;
        const stockCountEl = document.getElementById("metrics-stock-count");
        if (stockCountEl) stockCountEl.textContent = `${state.stockItems.length} Stock Items`;

        populateVoucherDropdowns();
        if (typeof populateGSTRegistrationChoices === 'function') populateGSTRegistrationChoices();
        populateReportLedgerSelect();
        loadDashboardData();
    } catch (err) {
        console.error("Error loading application state:", err);
        throw err;
    }
}

// -------------------------------------------------------------------------
// Gateway of Tally Navigation & Keyboard
// -------------------------------------------------------------------------
function initGatewayItems() {
    const items = document.querySelectorAll(".gateway-menu-item");
    state.gatewayItems = Array.from(items);
    state.selectedMenuIndex = 0;

    items.forEach((item, idx) => {
        item.addEventListener("click", () => {
            selectGatewayItem(idx);
            executeGatewayItem(item);
        });
    });

    const scrollContainer = document.getElementById("gateway-menu-scrollable");
    if (scrollContainer) {
        scrollContainer.scrollTop = 0;
    }
}

function selectGatewayItem(index) {
    if (index < 0) index = 0;
    if (index >= state.gatewayItems.length) index = state.gatewayItems.length - 1;
    state.selectedMenuIndex = index;

    state.gatewayItems.forEach((item, idx) => {
        if (idx === index) {
            item.classList.add("active");
            item.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } else {
            item.classList.remove("active");
        }
    });
}

function executeGatewayItem(item) {
    if (!item) return;
    const action = item.dataset.action;
    const target = item.dataset.target;

    if (action === "Vouchers") {
        openVoucherEntry("Payment");
    } else if (action === "Masters") {
        if (target === "create" || target === "alter") {
            openMasters("ledgers");
        }
    } else if (action === "Display") {
        if (target === "AccountsInfo") {
            openMasters("ledgers");
        } else if (target === "TDLStudio") {
            openTDLStudio();
        } else {
            const rawLabel = item.querySelector("span").innerText.replace(/[\n\r]/g, "");
            openReport(target, rawLabel);
        }
    } else if (action === "Quit") {
        if (confirm("Quit TallyForge?")) {
            alert("To close, you can simply close this browser tab or window.");
        }
    }
}

function setupKeyboardListeners() {
    window.addEventListener("keydown", (e) => {
        if (document.getElementById('company-modal').style.display === 'flex') {
            if (e.key === 'Escape') { e.preventDefault(); closeCompanyModal(); }
            return;
        }
        if (!companyReady) return;
        // Cmd+K / Ctrl+K / Alt+G: Global Command Palette
        if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
            e.preventDefault();
            openCommandPalette();
            return;
        }

        // Alt Combinations
        if (e.altKey && (e.key === "g" || e.key === "G")) { e.preventDefault(); openCommandPalette(); return; }
        if (e.altKey && (e.key === "k" || e.key === "K")) { e.preventDefault(); openCompanyModal(); return; }
        if (e.altKey && (e.key === "y" || e.key === "Y")) { e.preventDefault(); openDataModal(); return; }
        if (e.altKey && (e.key === "z" || e.key === "Z")) { e.preventDefault(); openExchangeModal(); return; }
        if (e.altKey && (e.key === "o" || e.key === "O")) { e.preventDefault(); openImportModal(); return; }
        if (e.altKey && (e.key === "e" || e.key === "E")) { e.preventDefault(); openExportModal(); return; }
        if (e.altKey && (e.key === "p" || e.key === "P")) { e.preventDefault(); openPrintModal(); return; }
        if (e.altKey && e.key === "F5") { e.preventDefault(); openVoucherEntry("Debit Note"); return; }
        if (e.altKey && e.key === "F6") { e.preventDefault(); openVoucherEntry("Credit Note"); return; }
        if (e.altKey && e.key === "F7") { e.preventDefault(); runManufacturingPrompt(); return; }

        // Ctrl Combinations
        if (e.ctrlKey && (e.key === "n" || e.key === "N")) { e.preventDefault(); toggleCalculator(true); return; }
        if (e.ctrlKey && (e.key === "m" || e.key === "M")) { e.preventDefault(); toggleCalculator(false); return; }
        if (e.ctrlKey && e.key === "F4") { e.preventDefault(); runPayrollPrompt(); return; }
        if (state.currentScreen === "voucher" && e.ctrlKey && (e.key === "a" || e.key === "A")) {
            e.preventDefault();
            saveVoucher();
            return;
        }

        // Esc: Return to Gateway or Close any Open Modal
        if (e.key === "Escape") {
            e.preventDefault();
            const modals = [
                "company-operations-modal", "command-palette-modal", "goto-modal", "company-modal", "data-modal", "export-modal",
                "print-modal", "exchange-modal", "import-modal", "features-modal", "help-modal", "auth-modal"
            ];
            for (const mId of modals) {
                const el = document.getElementById(mId);
                if (el && el.style.display === "flex") {
                    el.style.display = "none";
                    return;
                }
            }
            const calcDrawer = document.getElementById("calc-drawer");
            if (calcDrawer && calcDrawer.style.display === "flex") {
                toggleCalculator(false);
                return;
            }
            goBackToGateway();
            return;
        }

        // Function Keys
        if (e.key === "F1") { e.preventDefault(); openHelpModal(); return; }
        if (e.key === "F2") { e.preventDefault(); openDatePrompt(); return; }
        if (e.key === "F3") { e.preventDefault(); openCompanyModal(); return; }
        if (e.key === "F4") { e.preventDefault(); openVoucherEntry("Contra"); return; }
        if (e.key === "F5" && !e.altKey) { e.preventDefault(); openVoucherEntry("Payment"); return; }
        if (e.key === "F6" && !e.altKey) { e.preventDefault(); openVoucherEntry("Receipt"); return; }
        if (e.key === "F7" && !e.altKey) { e.preventDefault(); openVoucherEntry("Journal"); return; }
        if (e.key === "F8" && !e.altKey) { e.preventDefault(); openVoucherEntry("Sales"); return; }
        if (e.key === "F9" && !e.altKey) { e.preventDefault(); openVoucherEntry("Purchase"); return; }
        if (e.key === "F10") { e.preventDefault(); openMasters("stock_items"); return; }
        if (e.key === "F11") { e.preventDefault(); openFeaturesModal(); return; }
        if (e.key === "F12") { e.preventDefault(); openTDLStudio(); return; }

        // Gateway Screen Hotkeys
        const isInputFocused = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
        if (state.currentScreen === "gateway" && !isInputFocused) {
            if (e.key === "ArrowUp") {
                e.preventDefault();
                selectGatewayItem(state.selectedMenuIndex - 1);
            } else if (e.key === "ArrowDown") {
                e.preventDefault();
                selectGatewayItem(state.selectedMenuIndex + 1);
            } else if (e.key === "Enter") {
                e.preventDefault();
                executeGatewayItem(state.gatewayItems[state.selectedMenuIndex]);
            } else if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
                const pressedKey = e.key.toUpperCase();
                for (let i = 0; i < state.gatewayItems.length; i++) {
                    const item = state.gatewayItems[i];
                    const hk = item.querySelector(".hotkey");
                    if (hk && hk.textContent.trim().toUpperCase() === pressedKey) {
                        e.preventDefault();
                        selectGatewayItem(i);
                        executeGatewayItem(item);
                        break;
                    }
                }
            }
        }
    });
}

function goBackToGateway() {
    state.currentScreen = "gateway";
    const dash = document.getElementById("dashboard-screen");
    if (dash) {
        dash.style.display = "flex";
    }
    const gwBox = document.getElementById("gateway-box");
    if (gwBox) {
        gwBox.style.display = "flex";
    }
    document.getElementById("report-screen").style.display = "none";
    document.getElementById("voucher-screen").style.display = "none";
    document.getElementById("masters-screen").style.display = "none";
    document.getElementById("tdl-studio-screen").style.display = "none";
    document.getElementById("breadcrumb").textContent = "TallyForge Enterprise Hub // Dashboard";

    const activeItem = document.querySelector(".gateway-menu-item.active");
    if (activeItem) {
        activeItem.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else {
        const scrollContainer = document.getElementById("gateway-menu-scrollable");
        if (scrollContainer) scrollContainer.scrollTop = 0;
    }
    loadDashboardData();
}

function openDashboard() {
    goBackToGateway();
}

// -------------------------------------------------------------------------
// Reports Rendering (TDL Driven)
// -------------------------------------------------------------------------
async function openReport(reportName, reportTitle, fromDate, toDate) {
    if (_dashboardLoadTimeout) {
        clearTimeout(_dashboardLoadTimeout);
        _dashboardLoadTimeout = null;
    }
    state.currentScreen = "report";
    state.currentReportName = reportName;

    const dash = document.getElementById("dashboard-screen");
    if (dash) dash.style.display = "none";
    document.getElementById("gateway-box").style.display = "none";
    document.getElementById("voucher-screen").style.display = "none";
    document.getElementById("masters-screen").style.display = "none";
    document.getElementById("tdl-studio-screen").style.display = "none";
    document.getElementById("report-screen").style.display = "flex";

    document.getElementById("breadcrumb").textContent = `Gateway of Tally > ${reportTitle || reportName}`;
    document.getElementById("report-title").textContent = reportTitle || reportName;

    // Configure dynamic action button for enterprise modules
    const actionBtn = document.getElementById("report-action-btn");
    if (actionBtn) {
        const rnLower = reportName.toLowerCase();
        if (rnLower.includes("fixedasset") || rnLower.includes("asset")) {
            actionBtn.style.display = "inline-block";
            actionBtn.textContent = "+ Add Asset";
            actionBtn.onclick = () => openFixedAssetModal();
        } else if (rnLower.includes("order")) {
            actionBtn.style.display = "inline-block";
            actionBtn.textContent = "+ New Order";
            actionBtn.onclick = () => openOrderCreationModal();
        } else if (rnLower.includes("gstr2b") || rnLower.includes("itcrecon")) {
            actionBtn.style.display = "inline-block";
            actionBtn.textContent = "Reconcile 2B";
            actionBtn.onclick = () => openGSTR2BModal();
        } else if (rnLower.includes("landedcost")) {
            actionBtn.style.display = "inline-block";
            actionBtn.textContent = "+ Allocate Cost";
            actionBtn.onclick = () => openLandedCostModal();
        } else if (rnLower.includes("pos")) {
            actionBtn.style.display = "inline-block";
            actionBtn.textContent = "+ Fast POS Invoice";
            actionBtn.onclick = () => openPOSBillingModal();
        } else if (rnLower.includes("forex")) {
            actionBtn.style.display = "inline-block";
            actionBtn.textContent = "Post Revaluation";
            actionBtn.onclick = async () => {
                if (confirm("Post Foreign Exchange Revaluation Voucher now?")) {
                    try {
                        const r = await fetch("/api/forex/revalue", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ date: "2026-10-31" })
                        });
                        const j = await r.json();
                        if (r.ok) {
                            alert(j.result ? j.result.message : "Forex revaluation processed!");
                            openReport("ForexRevaluation");
                        } else {
                            alert("Error: " + (j.error || "Failed"));
                        }
                    } catch(e) {
                        alert("Network error: " + e);
                    }
                }
            };
        } else {
            actionBtn.style.display = "none";
        }
    }

    // Show or hide ledger filter for General Ledger
    const ledgerFilterDiv = document.getElementById("report-ledger-filter");
    if (reportName.toLowerCase().includes("generalledger")) {
        ledgerFilterDiv.style.display = "flex";
    } else {
        ledgerFilterDiv.style.display = "none";
    }

    const fromInput = document.getElementById("report-from-date");
    const toInput = document.getElementById("report-to-date");
    const fDate = fromDate !== undefined ? fromDate : (fromInput ? fromInput.value : "");
    const tDate = toDate !== undefined ? toDate : (toInput ? toInput.value : "");

    const thead = document.getElementById("report-table-head");
    const tbody = document.getElementById("report-table-body");
    thead.innerHTML = "";
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:25px;">Executing TDL definition & calculating balances...</td></tr>';

    try {
        let url = `/api/report/${encodeURIComponent(reportName)}`;
        const params = [];
        if (reportName.toLowerCase().includes("generalledger")) {
            params.push(`ledger=${encodeURIComponent(state.selectedLedgerName)}`);
        }
        if (fDate) params.push(`from_date=${encodeURIComponent(fDate)}`);
        if (tDate) params.push(`to_date=${encodeURIComponent(tDate)}`);
        if (params.length > 0) {
            url += `?${params.join("&")}`;
        }
        const res = await fetch(url);
        const reportData = await res.json();
        renderReportTable(reportData, reportName);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" style="color:red; text-align:center;">Error rendering TDL Report: ${err}</td></tr>`;
    }
}

// Drill into a report row (a month of a register, a sub-group): same report with extra parameters; "" goes back.
async function openReportDrill(reportName, drill) {
    const fromInput = document.getElementById("report-from-date");
    const toInput = document.getElementById("report-to-date");
    const params = [];
    if (drill) params.push(drill);
    if (fromInput && fromInput.value) params.push(`from_date=${encodeURIComponent(fromInput.value)}`);
    if (toInput && toInput.value) params.push(`to_date=${encodeURIComponent(toInput.value)}`);
    try {
        const res = await fetch(`/api/report/${encodeURIComponent(reportName)}${params.length ? "?" + params.join("&") : ""}`);
        renderReportTable(await res.json(), reportName);
    } catch (err) {
        alert("Could not open the report: " + err);
    }
}

function filterReportByDate() {
    const fromInput = document.getElementById("report-from-date");
    const toInput = document.getElementById("report-to-date");
    const fDate = fromInput ? fromInput.value : "";
    const tDate = toInput ? toInput.value : "";
    openReport(state.currentReportName, null, fDate, tDate);
}

function reloadGeneralLedger() {
    const sel = document.getElementById("report-ledger-select");
    if (sel && sel.value) {
        state.selectedLedgerName = sel.value;
        openReport("GeneralLedger", `General Ledger (${sel.value})`);
    }
}

function populateReportLedgerSelect() {
    const sel = document.getElementById("report-ledger-select");
    if (!sel) return;
    sel.innerHTML = "";
    state.ledgers.forEach(l => {
        const opt = document.createElement("option");
        opt.value = l.name;
        opt.textContent = `${l.name} (${l.group_name})`;
        if (l.name === state.selectedLedgerName) opt.selected = true;
        sel.appendChild(opt);
    });
}

function renderReportTable(reportData, reportName) {
    const thead = document.getElementById("report-table-head");
    const tbody = document.getElementById("report-table-body");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    if (!reportData || !reportData.parts || reportData.parts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:15px;">No records found.</td></tr>';
        return;
    }

    let headerRendered = false;

    reportData.parts.forEach(part => {
        (part.lines || []).forEach(line => {
            const isHeader = line.name.toLowerCase().includes("header");
            const isTotal = line.name.toLowerCase().includes("total");

            const tr = document.createElement("tr");
            if (isTotal) tr.className = "total-row";

            if (line.drill !== undefined && line.drill !== null && !isHeader && !isTotal) {
                tr.style.cursor = "pointer";
                tr.title = line.drill ? "Click to drill down" : "Click to go back";
                tr.onclick = () => openReportDrill(reportName, line.drill);
            } else if (line.voucher_id && !isHeader && !isTotal) {
                tr.style.cursor = "pointer";
                tr.title = `Click to view / alter Voucher #${line.voucher_id}`;
                tr.onclick = () => openVoucherForViewOrAlter(line.voucher_id);
            }

            line.fields.forEach(field => {
                const cell = isHeader ? document.createElement("th") : document.createElement("td");
                cell.textContent = field.display_value || "";
                if (field.align) cell.style.textAlign = field.align;
                if (field.width) cell.style.width = `${field.width}%`;

                // Interactive voucher alter link
                if (field.voucher_id) {
                    cell.style.cursor = "pointer";
                    cell.style.color = "#004d40";
                    cell.style.fontWeight = "bold";
                    cell.title = `Click to view or alter Voucher #${field.voucher_id}`;
                    cell.onclick = (e) => {
                        e.stopPropagation();
                        openVoucherForViewOrAlter(field.voucher_id);
                    };
                }

                // Interactive ledger drill-down in Balance Sheet / Trial Balance
                if (!isHeader && !isTotal && (field.name === "Particulars" || field.name === "LName" || field.name === "AName")) {
                    const matchLedger = state.ledgers.find(l => l.name === field.display_value);
                    if (matchLedger) {
                        cell.style.cursor = "pointer";
                        cell.style.color = "#00796b";
                        cell.style.textDecoration = "underline";
                        cell.title = `Drill-down to General Ledger (${matchLedger.name})`;
                        cell.onclick = (e) => {
                            e.stopPropagation();
                            openLedgerDrilldown(matchLedger.name);
                        };
                    }
                }

                tr.appendChild(cell);
            });

            if (isHeader && !headerRendered) {
                thead.appendChild(tr);
                headerRendered = true;
            } else {
                tbody.appendChild(tr);
            }
        });
    });
}

function openLedgerDrilldown(ledgerName) {
    if (!ledgerName) return;
    state.selectedLedgerName = ledgerName;
    const sel = document.getElementById("report-ledger-select");
    if (sel) sel.value = ledgerName;
    openReport("GeneralLedger", `General Ledger (${ledgerName})`);
}

async function openVoucherForViewOrAlter(voucherId) {
    if (!voucherId) return;
    try {
        const res = await fetch(`/api/voucher?voucher_id=${voucherId}`);
        const vch = await res.json();
        if (!res.ok || !vch.id) {
            alert(`Could not load voucher #${voucherId}: ${vch.error || 'Not found'}`);
            return;
        }

        openVoucherEntry(vch.voucher_type, true);
        const idInput = document.getElementById("vch-id");
        if (idInput) idInput.value = vch.id;
        const registration = document.getElementById('vch-gst-registration');
        if (registration) {registration.value = vch.gst_registration_id ? String(vch.gst_registration_id) : ''; registration.disabled = true;}
        document.getElementById("vch-title").textContent = `Accounting Voucher Alteration #${vch.id} (${vch.voucher_type})`;
        document.getElementById("breadcrumb").textContent = `Gateway of Tally > Accounting Voucher > Alter #${vch.id}`;
        document.getElementById("vch-date").value = vch.date;
        document.getElementById("vch-narration").value = vch.narration || "";
        document.getElementById("vch-amount").value = vch.total_amount || 0;

        const badge = document.getElementById("vch-number-badge");
        if (badge) badge.textContent = `Vch #${vch.id}`;

        const histBtn = document.getElementById("vch-history-btn");
        if (histBtn) histBtn.style.display = "block";

        // If voucher has more than 2 entries, switch automatically to multi-line mode
        if (vch.entries && vch.entries.length > 2) {
            toggleVoucherMode();
            state.multilineEntries = vch.entries.map(e => ({
                type: e.entry_type,
                ledger: e.ledger_name,
                debit: e.entry_type === "Dr" ? e.amount : 0,
                credit: e.entry_type === "Cr" ? e.amount : 0,
                cost_centre: e.cost_centre_name || ""
            }));
            document.getElementById("vch-ml-narration").value = vch.narration || "";
            renderVchMultilineRows();
        } else if (vch.entries && vch.entries.length >= 2) {
            const drEntry = vch.entries.find(e => e.entry_type === "Dr") || vch.entries[0];
            const crEntry = vch.entries.find(e => e.entry_type === "Cr") || vch.entries[1];
            if (drEntry) document.getElementById("vch-dr-ledger").value = drEntry.ledger_name;
            if (crEntry) document.getElementById("vch-cr-ledger").value = crEntry.ledger_name;
        }

        // Bill allocations
        if (vch.bill_allocations && vch.bill_allocations.length > 0) {
            document.getElementById("vch-bill-ref").value = vch.bill_allocations[0].ref_name || "";
        }

        // Inventory items
        if (vch.inventory_items && vch.inventory_items.length > 0) {
            const item = vch.inventory_items[0];
            document.getElementById("vch-stock-item").value = item.stock_item_name || "";
            document.getElementById("vch-stock-qty").value = item.qty || "";
            document.getElementById("vch-stock-rate").value = item.rate || "";
            if (item.godown_name) document.getElementById("vch-godown").value = item.godown_name;
        }
    } catch (e) {
        alert(`Error opening voucher: ${e.message}`);
    }
}

// -------------------------------------------------------------------------
// Accounting Voucher Entry Screen (Interactive Standard & Multi-Line Modes)
// -------------------------------------------------------------------------
state.voucherMode = "standard";
state.multilineEntries = [];

function openVoucherEntry(type, standardOnly) {
    if (_dashboardLoadTimeout) {
        clearTimeout(_dashboardLoadTimeout);
        _dashboardLoadTimeout = null;
    }
    state.currentScreen = "voucher";
    state.voucherType = type || "Payment";
    state.voucherMode = "standard";

    const vchIdInput = document.getElementById("vch-id");
    if (vchIdInput) vchIdInput.value = "";

    const registration = document.getElementById('vch-gst-registration');
    if (registration) {registration.value = '';registration.disabled = false;}
    const historyBtn = document.getElementById("vch-history-btn");
    if (historyBtn) historyBtn.style.display = "none";

    const dash = document.getElementById("dashboard-screen");
    if (dash) dash.style.display = "none";
    document.getElementById("gateway-box").style.display = "none";
    document.getElementById("report-screen").style.display = "none";
    document.getElementById("masters-screen").style.display = "none";
    document.getElementById("tdl-studio-screen").style.display = "none";
    document.getElementById("voucher-screen").style.display = "flex";

    document.getElementById("breadcrumb").textContent = `Gateway of Tally > Accounting Voucher > ${state.voucherType}`;
    document.getElementById("vch-title").textContent = `Accounting Voucher Creation (${state.voucherType})`;
    
    const vchSelect = document.getElementById("vch-type-select");
    if (vchSelect) vchSelect.value = state.voucherType;

    document.getElementById("vch-date").value = new Date().toISOString().split("T")[0];
    document.getElementById("vch-amount").value = "";
    document.getElementById("vch-bill-ref").value = "";
    document.getElementById("vch-narration").value = "";
    document.getElementById("vch-stock-qty").value = "";
    document.getElementById("vch-stock-rate").value = "";

    const badge = document.getElementById("vch-number-badge");
    if (badge) badge.textContent = "Auto #";

    // Reset modes
    const stdMode = document.getElementById("vch-standard-mode");
    const mlMode = document.getElementById("vch-multiline-mode");
    const toggleBtn = document.getElementById("vch-mode-toggle-btn");
    if (stdMode) stdMode.style.display = "block";
    if (mlMode) mlMode.style.display = "none";
    if (toggleBtn) toggleBtn.textContent = "Switch to Multi-Line Grid";

    populateVoucherDropdowns();
    applyVoucherTypeLedgerFilter();
    updateVchLedgerBalances();
    initMultilineEntries();
    refreshVoucherNumberBadge();
    const dateInput = document.getElementById("vch-date");
    if (dateInput) dateInput.onchange = refreshVoucherNumberBadge;
    // Sales, Purchase and the two notes are entered as invoices (several item lines, charges, GST); altering keeps the old screen.
    const toggleBtn2 = document.getElementById("vch-mode-toggle-btn");
    if (!standardOnly && INVOICE_TYPES.includes(state.voucherType)) openInvoiceMode(state.voucherType);
    else {
        document.getElementById("vch-invoice-mode").style.display = "none";
        if (toggleBtn2) toggleBtn2.style.display = "";
    }
}

// Shows the number the voucher will get when saved (the next in the series), like TallyPrime's "No." field.
async function refreshVoucherNumberBadge() {
    const badge = document.getElementById("vch-number-badge");
    const idInput = document.getElementById("vch-id");
    if (!badge || (idInput && idInput.value)) return;          // an open voucher keeps its own number
    try {
        const date = document.getElementById("vch-date").value || "";
        const res = await fetch(`/api/vouchers/next_number?voucher_type=${encodeURIComponent(state.voucherType)}&date=${encodeURIComponent(date)}`);
        if (res.ok) badge.textContent = "No. " + (await res.json()).number;
    } catch (err) { /* the badge keeps its "Auto #" text */ }
}

// For a company imported from TallyPrime, Payment / Receipt / Contra offer only cash and bank ledgers on the side the rule covers.
async function applyVoucherTypeLedgerFilter() {
    if (!state.voucherRules) {
        try { state.voucherRules = await (await fetch("/api/vouchers/rules")).json(); } catch (err) { state.voucherRules = {strict: false, cash_bank: []}; }
    }
    const rules = state.voucherRules;
    ["vch-dr-ledger", "vch-cr-ledger"].forEach(id => {            // undo the filter of a previous voucher type first
        const select = document.getElementById(id);
        if (select) [...select.options].forEach(o => { o.hidden = false; o.disabled = false; });
    });
    if (!rules.strict) return;
    const allowed = new Set(rules.cash_bank);
    const filterSelect = (id) => {
        const select = document.getElementById(id);
        if (!select) return;
        const keep = select.value;
        [...select.options].forEach(o => { o.hidden = !allowed.has(o.value); o.disabled = !allowed.has(o.value); });
        if (!allowed.has(keep)) { const first = [...select.options].find(o => allowed.has(o.value)); if (first) select.value = first.value; }
    };
    const type = state.voucherType;
    if (type === "Payment") filterSelect("vch-cr-ledger");
    else if (type === "Receipt") filterSelect("vch-dr-ledger");
    else if (type === "Contra") { filterSelect("vch-dr-ledger"); filterSelect("vch-cr-ledger"); }
}

function toggleVoucherMode() {
    const stdMode = document.getElementById("vch-standard-mode");
    const mlMode = document.getElementById("vch-multiline-mode");
    const toggleBtn = document.getElementById("vch-mode-toggle-btn");

    if (state.voucherMode === "standard") {
        state.voucherMode = "multiline";
        stdMode.style.display = "none";
        mlMode.style.display = "flex";
        toggleBtn.textContent = "Switch to Standard Invoice";
        // Transfer current standard values to multiline if set
        const curAmount = parseFloat(document.getElementById("vch-amount").value) || 0;
        const curDr = document.getElementById("vch-dr-ledger").value;
        const curCr = document.getElementById("vch-cr-ledger").value;
        const curCC = document.getElementById("vch-cost-centre").value;
        if (curAmount > 0) {
            state.multilineEntries = [
                { type: "Dr", ledger: curDr, debit: curAmount, credit: 0, cost_centre: curCC },
                { type: "Cr", ledger: curCr, debit: 0, credit: curAmount, cost_centre: curCC }
            ];
        }
        renderVchMultilineRows();
    } else {
        state.voucherMode = "standard";
        stdMode.style.display = "block";
        mlMode.style.display = "none";
        toggleBtn.textContent = "Switch to Multi-Line Grid";
    }
}

function initMultilineEntries() {
    const drLedger = state.ledgers.length > 0 ? state.ledgers[0].name : "";
    const crLedger = state.ledgers.length > 1 ? state.ledgers[1].name : drLedger;
    state.multilineEntries = [
        { type: "Dr", ledger: drLedger, debit: 0, credit: 0, cost_centre: "" },
        { type: "Cr", ledger: crLedger, debit: 0, credit: 0, cost_centre: "" }
    ];
    renderVchMultilineRows();
}

function renderVchMultilineRows() {
    const tbody = document.getElementById("ml-table-body");
    if (!tbody) return;
    tbody.innerHTML = "";

    state.multilineEntries.forEach((row, idx) => {
        const tr = document.createElement("tr");

        const ledgerOptions = state.ledgers.map(l => 
            `<option value="${l.name}" ${l.name === row.ledger ? 'selected' : ''}>${l.name} [${l.group_name}]</option>`
        ).join("");

        const ccOptions = `<option value="">-- None --</option>` + state.costCentres.map(cc => 
            `<option value="${cc.name}" ${cc.name === row.cost_centre ? 'selected' : ''}>${cc.name}</option>`
        ).join("");

        tr.innerHTML = `
            <td>
                <select class="tally-input" style="font-weight:bold; padding:2px;" onchange="updateMultilineRow(${idx}, 'type', this.value)">
                    <option value="Dr" ${row.type === 'Dr' ? 'selected' : ''}>Dr (By)</option>
                    <option value="Cr" ${row.type === 'Cr' ? 'selected' : ''}>Cr (To)</option>
                </select>
            </td>
            <td>
                <select class="tally-input" style="width:100%;" onchange="updateMultilineRow(${idx}, 'ledger', this.value)">
                    ${ledgerOptions}
                </select>
            </td>
            <td>
                <input type="number" class="tally-input" style="text-align:right; width:100%;" step="0.01" 
                    value="${row.debit || ''}" placeholder="0.00" 
                    ${row.type === 'Cr' ? 'disabled style="background:#eee;"' : ''}
                    oninput="updateMultilineRow(${idx}, 'debit', this.value)">
            </td>
            <td>
                <input type="number" class="tally-input" style="text-align:right; width:100%;" step="0.01" 
                    value="${row.credit || ''}" placeholder="0.00" 
                    ${row.type === 'Dr' ? 'disabled style="background:#eee;"' : ''}
                    oninput="updateMultilineRow(${idx}, 'credit', this.value)">
            </td>
            <td>
                <select class="tally-input" style="width:100%; font-size:0.75rem;" onchange="updateMultilineRow(${idx}, 'cost_centre', this.value)">
                    ${ccOptions}
                </select>
            </td>
            <td style="text-align:center;">
                <button onclick="removeMultilineRow(${idx})" style="background:#c62828; color:#fff; border:none; padding:1px 6px; cursor:pointer;" title="Remove row">✕</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    onMultilineEntryChanged();
}

function addMultilineRow(type = "Dr", ledger = "", debit = 0, credit = 0, cost_centre = "") {
    const defaultLedger = ledger || (state.ledgers.length > 0 ? state.ledgers[0].name : "");
    state.multilineEntries.push({
        type: type,
        ledger: defaultLedger,
        debit: parseFloat(debit) || 0,
        credit: parseFloat(credit) || 0,
        cost_centre: cost_centre
    });
    renderVchMultilineRows();
}

function removeMultilineRow(idx) {
    if (state.multilineEntries.length <= 1) {
        alert("A voucher must have at least one debit and one credit entry!");
        return;
    }
    state.multilineEntries.splice(idx, 1);
    renderVchMultilineRows();
}

function updateMultilineRow(idx, field, value) {
    const row = state.multilineEntries[idx];
    if (!row) return;

    if (field === "type") {
        row.type = value;
        if (value === "Dr") {
            row.debit = row.credit || 0;
            row.credit = 0;
        } else {
            row.credit = row.debit || 0;
            row.debit = 0;
        }
        renderVchMultilineRows();
        return;
    } else if (field === "debit") {
        row.debit = parseFloat(value) || 0;
    } else if (field === "credit") {
        row.credit = parseFloat(value) || 0;
    } else if (field === "ledger") {
        row.ledger = value;
    } else if (field === "cost_centre") {
        row.cost_centre = value;
    }

    onMultilineEntryChanged();
}

function onMultilineEntryChanged() {
    let totDr = 0;
    let totCr = 0;
    state.multilineEntries.forEach(r => {
        if (r.type === "Dr") totDr += (parseFloat(r.debit) || 0);
        if (r.type === "Cr") totCr += (parseFloat(r.credit) || 0);
    });

    const diff = Math.round((totDr - totCr) * 100) / 100;
    const drEl = document.getElementById("ml-total-dr");
    const crEl = document.getElementById("ml-total-cr");
    const varEl = document.getElementById("ml-variance");

    if (drEl) drEl.textContent = `₹${totDr.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
    if (crEl) crEl.textContent = `₹${totCr.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
    if (varEl) {
        if (Math.abs(diff) < 0.01) {
            varEl.textContent = `₹0.00 (Balanced)`;
            varEl.style.color = "#2e7d32";
        } else {
            varEl.textContent = `₹${diff.toFixed(2)} (${diff > 0 ? 'Dr Excess' : 'Cr Excess'})`;
            varEl.style.color = "#c62828";
        }
    }
}

function autoBalanceMultiline() {
    let totDr = 0;
    let totCr = 0;
    state.multilineEntries.forEach(r => {
        if (r.type === "Dr") totDr += (parseFloat(r.debit) || 0);
        if (r.type === "Cr") totCr += (parseFloat(r.credit) || 0);
    });
    const diff = Math.round((totDr - totCr) * 100) / 100;
    if (Math.abs(diff) < 0.01) {
        alert("Voucher entries are already balanced!");
        return;
    }

    if (diff > 0) {
        // Debit exceeds credit, add balancing Credit row
        const crLedger = state.ledgers.find(l => l.name === "Sales A/c" || l.group_name === "Sundry Creditors" || l.group_name === "Bank Accounts") || state.ledgers[0];
        addMultilineRow("Cr", crLedger.name, 0, diff);
    } else {
        // Credit exceeds debit, add balancing Debit row
        const drLedger = state.ledgers.find(l => l.name === "Purchase A/c" || l.group_name === "Sundry Debtors" || l.group_name === "Bank Accounts") || state.ledgers[0];
        addMultilineRow("Dr", drLedger.name, Math.abs(diff), 0);
    }
}

async function addGSTLinesToMultiline() {
    // Taxable value = the lines that are not tax ledgers; the server decides CGST + SGST or IGST from the party's state,
    // finds the company's own tax ledgers and gives the side (credit for output tax, debit for input tax).
    const isTax = name => /\b(cgst|sgst|utgst|igst)\b/i.test(name || "");
    let taxable = 0, party = null;
    state.multilineEntries.forEach(r => {
        const ledger = state.ledgers.find(l => l.name === r.ledger);
        if (ledger && /^Sundry (Debtors|Creditors)$/.test(ledger.group_name || "")) { party = party || ledger.name; return; }
        if (!isTax(r.ledger)) taxable += (parseFloat(r.credit) || 0) + (parseFloat(r.debit) || 0);
    });
    if (taxable <= 0) {
        alert("Enter the sales or purchase amount first, then calculate GST.");
        return;
    }
    const rateText = prompt("GST rate for this value (%):", "18");
    if (rateText === null) return;
    const rate = parseFloat(rateText);
    if (!(rate > 0)) { alert("Enter a GST rate above zero."); return; }
    try {
        const res = await fetch("/api/gst/suggest", {method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({voucher_type: state.voucherType, party_ledger: party, taxable: {[rate]: taxable}})});
        const out = await res.json();
        if (!res.ok) { alert(out.error || "Could not calculate GST"); return; }
        out.tax_lines.forEach(t => {
            if (!t.ledger_name) return;
            addMultilineRow(t.entry_type, t.ledger_name, t.entry_type === "Dr" ? t.amount : 0, t.entry_type === "Cr" ? t.amount : 0);
        });
        if (out.warnings.length) alert(out.warnings.join("\n"));
        else alert(`${out.supply}: tax \u20B9${out.total_tax.toFixed(2)}, invoice total \u20B9${out.invoice_total.toFixed(2)}`);
    } catch (err) {
        alert("Could not calculate GST: " + err);
    }
}

function autoCalculateGSTPrompt() {
    const amtInput = document.getElementById("vch-amount");
    const baseVal = parseFloat(amtInput.value) || 0;
    if (baseVal <= 0) {
        const p = prompt("Enter base taxable amount (₹):", "10000");
        if (!p) return;
        const b = parseFloat(p);
        const totalWithGst = Math.round((b * 1.18) * 100) / 100;
        amtInput.value = totalWithGst;
        alert(`Computed 18% GST (9% CGST: ₹${(b*0.09).toFixed(2)}, 9% SGST: ₹${(b*0.09).toFixed(2)}). Total Voucher Amount set to ₹${totalWithGst}`);
    } else {
        const totalWithGst = Math.round((baseVal * 1.18) * 100) / 100;
        if (confirm(`Taxable base is ₹${baseVal}. Add 18% GST (CGST 9% + SGST 9%) to make Total ₹${totalWithGst}?`)) {
            amtInput.value = totalWithGst;
        }
    }
}

function onStockItemSelectChanged() {
    const itemSelect = document.getElementById("vch-stock-item");
    const itemName = itemSelect.value;
    const item = state.stockItems.find(i => i.name === itemName);
    if (item && item.opening_rate) {
        const rateInput = document.getElementById("vch-stock-rate");
        if (rateInput && (!rateInput.value || parseFloat(rateInput.value) === 0)) {
            rateInput.value = item.opening_rate;
        }
    }
    onStockQtyRateChanged();
}

function onStockQtyRateChanged() {
    const qty = parseFloat(document.getElementById("vch-stock-qty").value) || 0;
    const rate = parseFloat(document.getElementById("vch-stock-rate").value) || 0;
    if (qty > 0 && rate > 0) {
        const lineTotal = Math.round((qty * rate) * 100) / 100;
        const amtInput = document.getElementById("vch-amount");
        if (amtInput && (!amtInput.value || parseFloat(amtInput.value) === 0)) {
            amtInput.value = lineTotal;
        }
    }
}

async function openVoucherAuditHistory() {
    const vchId = document.getElementById("vch-id").value;
    if (!vchId) return;
    openAuditHistoryModal("voucher", parseInt(vchId));
}

function onVoucherTypeChanged(newType) {
    state.voucherType = newType;
    document.getElementById("breadcrumb").textContent = `Gateway of Tally > Accounting Voucher > ${state.voucherType}`;
    document.getElementById("vch-title").textContent = `Accounting Voucher Creation (${state.voucherType})`;

    // Filter ledgers intelligently based on voucher type
    const drLabel = document.getElementById("vch-dr-label");
    const crLabel = document.getElementById("vch-cr-label");
    if (newType === "Sales") {
        if (drLabel) drLabel.textContent = "Debit (Party / Debtor):";
        if (crLabel) crLabel.textContent = "Credit (Sales A/c):";
        // Auto select debtor and sales if found
        const debtor = state.ledgers.find(l => l.group_name === "Sundry Debtors");
        const sales = state.ledgers.find(l => l.name === "Sales A/c" || l.group_name.includes("Sales"));
        if (debtor) document.getElementById("vch-dr-ledger").value = debtor.name;
        if (sales) document.getElementById("vch-cr-ledger").value = sales.name;
    } else if (newType === "Purchase") {
        if (drLabel) drLabel.textContent = "Debit (Purchase A/c):";
        if (crLabel) crLabel.textContent = "Credit (Party / Creditor):";
        const purch = state.ledgers.find(l => l.name === "Purchase A/c" || l.group_name.includes("Purchase"));
        const creditor = state.ledgers.find(l => l.group_name === "Sundry Creditors");
        if (purch) document.getElementById("vch-dr-ledger").value = purch.name;
        if (creditor) document.getElementById("vch-cr-ledger").value = creditor.name;
    } else if (newType === "Payment") {
        if (drLabel) drLabel.textContent = "Debit (Expense / Vendor):";
        if (crLabel) crLabel.textContent = "Credit (Bank / Cash):";
    } else if (newType === "Receipt") {
        if (drLabel) drLabel.textContent = "Debit (Bank / Cash):";
        if (crLabel) crLabel.textContent = "Credit (Customer / Income):";
    }
    updateVchLedgerBalances();
    applyVoucherTypeLedgerFilter();
    refreshVoucherNumberBadge();
    const idInput = document.getElementById("vch-id");
    if (!(idInput && idInput.value)) {            // a new voucher: switch between the invoice screen and the plain one with the type
        const invoiceMode = document.getElementById("vch-invoice-mode");
        const toggle = document.getElementById("vch-mode-toggle-btn");
        if (INVOICE_TYPES.includes(newType)) openInvoiceMode(newType);
        else if (invoiceMode && invoiceMode.style.display !== "none") {
            invoiceMode.style.display = "none";
            document.getElementById("vch-standard-mode").style.display = "block";
            state.voucherMode = "standard";
            if (toggle) toggle.style.display = "";
        }
    }
}

function populateVoucherDropdowns() {
    const drSelect = document.getElementById("vch-dr-ledger");
    const crSelect = document.getElementById("vch-cr-ledger");
    const itemSelect = document.getElementById("vch-stock-item");
    const ccSelect = document.getElementById("vch-cost-centre");

    if (!drSelect || !crSelect) return;

    drSelect.innerHTML = "";
    crSelect.innerHTML = "";

    // Godowns come from the company's own master (the page used to offer fixed demo godowns that most companies do not have).
    const godownSelect = document.getElementById("vch-godown");
    if (godownSelect) {
        const keep = godownSelect.value;
        const names = (state.godowns || []).map(g => g.name).filter(Boolean);
        if (!names.length) names.push("Main Location");
        godownSelect.innerHTML = "";
        names.forEach(n => { const o = document.createElement("option"); o.value = o.textContent = n; godownSelect.appendChild(o); });
        godownSelect.value = names.includes(keep) ? keep : names[0];
    }

    state.ledgers.forEach((l) => {
        const opt1 = document.createElement("option");
        opt1.value = l.name;
        opt1.textContent = `${l.name} [${l.group_name}]`;
        drSelect.appendChild(opt1);

        const opt2 = document.createElement("option");
        opt2.value = l.name;
        opt2.textContent = `${l.name} [${l.group_name}]`;
        crSelect.appendChild(opt2);
    });

    if (state.ledgers.length > 1) {
        crSelect.selectedIndex = 1;
    }

    if (itemSelect) {
        itemSelect.innerHTML = '<option value="">-- No Inventory Allocation / Services --</option>';
        state.stockItems.forEach(i => {
            const opt = document.createElement("option");
            opt.value = i.name;
            opt.textContent = `${i.name} (Stock: ${i.closing_qty || 0} ${i.uom_symbol || 'Nos'})`;
            itemSelect.appendChild(opt);
        });
    }

    if (ccSelect) {
        ccSelect.innerHTML = '<option value="">-- No Cost Centre Allocation --</option>';
        state.costCentres.forEach(cc => {
            const opt = document.createElement("option");
            opt.value = cc.name;
            opt.textContent = `${cc.name} [${cc.category_name}]`;
            ccSelect.appendChild(opt);
        });
    }
}

function updateVchLedgerBalances() {
    const drName = document.getElementById("vch-dr-ledger").value;
    const crName = document.getElementById("vch-cr-ledger").value;

    const drLedger = state.ledgers.find(l => l.name === drName);
    const crLedger = state.ledgers.find(l => l.name === crName);

    if (drLedger) {
        document.getElementById("vch-dr-bal").textContent = `Cur Bal: ₹${drLedger.closing_balance.toLocaleString()} ${drLedger.closing_balance_type}`;
    }
    if (crLedger) {
        document.getElementById("vch-cr-bal").textContent = `Cur Bal: ₹${crLedger.closing_balance.toLocaleString()} ${crLedger.closing_balance_type}`;
    }
}

async function saveVoucher() {
    if (state.voucherMode === "invoice") { await invoiceSave(); return; }
    let payload = {};
    const date = document.getElementById("vch-date").value;
    const currency = document.getElementById("vch-currency").value;

    if (state.voucherMode === "multiline") {
        let totDr = 0;
        let totCr = 0;
        const entries = [];
        for (const r of state.multilineEntries) {
            const amt = r.type === "Dr" ? parseFloat(r.debit) : parseFloat(r.credit);
            if (amt > 0) {
                entries.push({
                    ledger_name: r.ledger,
                    entry_type: r.type,
                    amount: amt,
                    cost_centre_name: r.cost_centre
                });
                if (r.type === "Dr") totDr += amt;
                if (r.type === "Cr") totCr += amt;
            }
        }
        if (entries.length < 2) {
            alert("Please configure at least 2 entries (1 Debit and 1 Credit)!");
            return;
        }
        const diff = Math.abs(totDr - totCr);
        if (diff > 0.01) {
            alert(`Double entry is unbalanced! Total Debit (₹${totDr.toFixed(2)}) != Total Credit (₹${totCr.toFixed(2)}). Difference: ₹${diff.toFixed(2)}`);
            return;
        }
        const narration = document.getElementById("vch-ml-narration").value;
        payload = {
            voucher_type: state.voucherType,
            date: date,
            narration: narration,
            currency_symbol: currency,
            entries: entries
        };
    } else {
        const drLedger = document.getElementById("vch-dr-ledger").value;
        const crLedger = document.getElementById("vch-cr-ledger").value;
        const amount = parseFloat(document.getElementById("vch-amount").value);
        const narration = document.getElementById("vch-narration").value;
        const billRef = document.getElementById("vch-bill-ref").value;
        const godown = document.getElementById("vch-godown").value;
        const costCentre = document.getElementById("vch-cost-centre").value;

        if (!amount || amount <= 0) {
            alert("Please enter a valid amount!");
            return;
        }
        if (drLedger === crLedger) {
            alert("Debit and Credit ledgers cannot be identical!");
            return;
        }

        payload = {
            voucher_type: state.voucherType,
            date: date,
            narration: narration,
            currency_symbol: currency,
            entries: [
                { ledger_name: drLedger, entry_type: "Dr", amount: amount, cost_centre_name: costCentre },
                { ledger_name: crLedger, entry_type: "Cr", amount: amount, cost_centre_name: costCentre }
            ]
        };

        if (billRef) {
            payload.bill_allocations = [
                { ledger_name: drLedger, ref_type: "New Ref", ref_name: billRef, amount: amount, direction: "Dr" }
            ];
        }

        const stockItem = document.getElementById("vch-stock-item").value;
        const stockQty = parseFloat(document.getElementById("vch-stock-qty").value);
        const stockRate = parseFloat(document.getElementById("vch-stock-rate").value);
        if (stockItem && stockQty > 0) {
            payload.inventory_items = [{
                stock_item_name: stockItem,
                godown_name: godown,
                qty: stockQty,
                rate: stockRate || (amount / stockQty),
                amount: amount,
                direction: (state.voucherType === "Purchase" || state.voucherType === "Receipt") ? "In" : "Out"
            }];
        }
    }

    const registrationInput = document.getElementById('vch-gst-registration');
    if (registrationInput && registrationInput.value) payload.gst_registration_id = Number(registrationInput.value);
    const vchIdInput = document.getElementById("vch-id");
    const existingVchId = vchIdInput && vchIdInput.value ? parseInt(vchIdInput.value) : null;

    if (existingVchId) {
        payload.voucher_id = existingVchId;
        payload.user_name = state.currentUser || "Admin";
        try {
            const res = await fetch("/api/vouchers/alter", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const resData = await res.json();
            if (res.ok) {
                alert(`✓ Voucher #${existingVchId} Altered and Updated Successfully!`);
                vchIdInput.value = "";
                await loadAppState();
                goBackToGateway();
            } else {
                alert(`Voucher Alteration Error: ${resData.error}`);
            }
        } catch (err) {
            alert(`Failed to alter voucher: ${err}`);
        }
        return;
    }

    try {
        const res = await fetch("/api/vouchers", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const resData = await res.json();
        if (res.ok) {
            alert(`✓ Voucher Accepted! Voucher ID: ${resData.voucher_id}`);
            await loadAppState();
            goBackToGateway();
        } else {
            alert(`Voucher Error: ${resData.error}`);
        }
    } catch (err) {
        alert(`Failed to save voucher: ${err}`);
    }
}

async function cancelVoucherPrompt() {
    const vId = prompt("Enter Voucher ID to cancel:");
    if (!vId) return;
    const reason = prompt("Enter cancellation reason:", "User Requested Cancellation") || "Cancelled";
    try {
        const res = await fetch("/api/vouchers/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ voucher_id: parseInt(vId), reason: reason })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`Voucher #${vId} cancelled successfully!`);
            await loadAppState();
            goBackToGateway();
        } else {
            alert(`Error cancelling voucher: ${data.error}`);
        }
    } catch (err) {
        alert(`Request failed: ${err}`);
    }
}

// -------------------------------------------------------------------------
// Statutory Utilities & Prompts
// -------------------------------------------------------------------------
async function downloadGSTR1JSON() {
    try {
        const res = await fetch("/api/gst/gstr1_json");
        const data = await res.json();
        const jsonStr = JSON.stringify(data, null, 2);
        const blob = new Blob([jsonStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `GSTR1_${data.gstin || '29AABCT1332L1Z5'}_${data.fp || '102026'}.json`;
        a.click();
    } catch (err) {
        alert(`Error downloading GSTR-1 JSON: ${err}`);
    }
}

async function runManufacturingPrompt() {
    const bomName = "Assembled Desktop Combo";
    const qty = parseFloat(prompt(`Enter Quantity of finished units for BOM [${bomName}]:`, "5") || "0");
    if (!qty || qty <= 0) return;

    try {
        const res = await fetch("/api/manufacturing/produce", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                bom_name: bomName,
                qty: qty,
                date: new Date().toISOString().split("T")[0]
            })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`Manufacturing Journal Completed!\nVoucher ID: ${data.voucher_id}\nProduced: ${qty} units of ${bomName}`);
            await loadAppState();
        } else {
            alert(`Manufacturing Error: ${data.error}`);
        }
    } catch (err) {
        alert(`Request failed: ${err}`);
    }
}

async function runPayrollPrompt() {
    const month = prompt("Enter Payroll Month (e.g. October 2026):", "October 2026");
    if (!month) return;

    try {
        const res = await fetch("/api/payroll/process", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                month: month,
                date: new Date().toISOString().split("T")[0]
            })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`Payroll Processed Successfully!\nVoucher ID: ${data.voucher_id}\nDisbursed for: ${month}`);
            await loadAppState();
        } else {
            alert(`Payroll Error: ${data.error}`);
        }
    } catch (err) {
        alert(`Request failed: ${err}`);
    }
}

async function uploadBankStatementPrompt() {
    const sampleCsv = "Date,Description,ChequeNo,Debit,Credit,Balance\n2026-10-31,Cheque Clearing,CHQ-98124,0,25000,50000";
    const csvContent = prompt("Paste Bank Statement CSV text:", sampleCsv);
    if (!csvContent) return;

    try {
        const res = await fetch("/api/banking/upload_statement", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                bank_ledger: "HDFC Bank",
                csv_content: csvContent
            })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`BRS Auto-Reconciliation Complete!\nMatched: ${data.summary.matched} entries\nTotal Processed: ${data.summary.total_lines} lines`);
            await loadAppState();
            openReport("BankReconciliation", "Banking & BRS");
        } else {
            alert(`Reconciliation error: ${data.error}`);
        }
    } catch (err) {
        alert(`Error reconciling statement: ${err}`);
    }
}

async function createBackup() {
    try {
        const res = await fetch("/api/backup/create", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            alert(`100% Local Backup Created Successfully!\nSaved to: ${data.backup_file}`);
            await loadAppState();
        } else {
            alert(`Backup Failed: ${data.error}`);
        }
    } catch (err) {
        alert(`Backup error: ${err}`);
    }
}

async function restoreBackupPrompt() {
    const backupPath = prompt("Enter absolute path to backup SQLite .db file to restore:");
    if (!backupPath) return;

    try {
        const res = await fetch("/api/backup/restore", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ backup_file: backupPath })
        });
        const data = await res.json();
        if (res.ok) {
            alert("Database Restored Successfully! Reloading system state...");
            await loadAppState();
            goBackToGateway();
        } else {
            alert(`Restore Failed: ${data.error}`);
        }
    } catch (err) {
        alert(`Restore error: ${err}`);
    }
}

// -------------------------------------------------------------------------
// Masters Screen (Ledgers & Inventory Stock Items)
// -------------------------------------------------------------------------
let currentMasterType = "ledgers";

async function openMasters(type) {
    if (_dashboardLoadTimeout) {
        clearTimeout(_dashboardLoadTimeout);
        _dashboardLoadTimeout = null;
    }
    currentMasterType = type || "ledgers";
    state.currentScreen = "masters";

    const dash = document.getElementById("dashboard-screen");
    if (dash) dash.style.display = "none";
    document.getElementById("gateway-box").style.display = "none";
    document.getElementById("report-screen").style.display = "none";
    document.getElementById("voucher-screen").style.display = "none";
    document.getElementById("tdl-studio-screen").style.display = "none";
    document.getElementById("masters-screen").style.display = "flex";

    // Highlight active master tab
    const masterTypes = ['ledgers', 'groups', 'stock_items', 'units', 'godowns', 'employees', 'cost_centres', 'tax_configs'];
    masterTypes.forEach(t => {
        const btn = document.getElementById(`master-tab-${t}`);
        if (btn) btn.style.background = (t === currentMasterType) ? "#00796b" : "#004d40";
    });

    const thead = document.getElementById("masters-table-head");
    const tbody = document.getElementById("masters-table-body");
    thead.innerHTML = "";
    tbody.innerHTML = "<tr><td colspan='6' style='text-align:center; padding:12px;'>Loading master data...</td></tr>";

    if (currentMasterType === "ledgers") {
        document.getElementById("masters-title").textContent = "Chart of Accounts - Ledgers Master";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Ledgers";

        thead.innerHTML = `
            <tr>
                <th>Ledger Name</th>
                <th>Under Group</th>
                <th style="text-align:right;">Opening Balance</th>
                <th style="text-align:right;">Current Balance</th>
            </tr>
        `;
        tbody.innerHTML = "";
        state.ledgers.forEach(l => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${l.name}</strong></td>
                <td>${l.group_name}</td>
                <td style="text-align:right;">₹${l.opening_balance.toLocaleString()} ${l.opening_balance_type}</td>
                <td style="text-align:right; font-weight:bold;">₹${l.closing_balance.toLocaleString()} ${l.closing_balance_type}</td>
            `;
            tbody.appendChild(tr);
        });

    } else if (currentMasterType === "groups") {
        document.getElementById("masters-title").textContent = "Chart of Accounts - Accounting Groups";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Groups";

        thead.innerHTML = `
            <tr>
                <th>Group Name</th>
                <th>Under Group (Parent)</th>
                <th>Type</th>
                <th>Affects Gross Profit</th>
            </tr>
        `;
        try {
            const res = await fetch("/api/groups");
            const groups = await res.json();
            tbody.innerHTML = "";
            groups.forEach(g => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>${g.name}</strong></td>
                    <td>${g.parent || 'Primary'}</td>
                    <td>${g.is_revenue ? '<span style="color:#2e7d32;">Revenue (P&L)</span>' : '<span style="color:#1565c0;">Capital / Balance Sheet</span>'}</td>
                    <td>${g.affects_gross ? 'Yes' : 'No'}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan='4' style='color:red;'>Failed to load groups: ${e}</td></tr>`;
        }

    } else if (currentMasterType === "stock_items") {
        document.getElementById("masters-title").textContent = "Inventory Stock Items Master";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Stock Items";

        thead.innerHTML = `
            <tr>
                <th>Stock Item Name</th>
                <th>Group</th>
                <th>UOM</th>
                <th>HSN</th>
                <th>GST Rate</th>
                <th style="text-align:right;">Closing Qty</th>
                <th style="text-align:right;">Closing Value</th>
            </tr>
        `;
        tbody.innerHTML = "";
        state.stockItems.forEach(i => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${i.name}</strong></td>
                <td>${i.group_name}</td>
                <td>${i.uom_symbol}</td>
                <td>${i.hsn_code || '84433100'}</td>
                <td>${i.gst_rate || 18}%</td>
                <td style="text-align:right;">${i.closing_qty}</td>
                <td style="text-align:right; font-weight:bold;">₹${i.closing_value.toLocaleString()}</td>
            `;
            tbody.appendChild(tr);
        });

    } else if (currentMasterType === "units") {
        document.getElementById("masters-title").textContent = "Units of Measure (UOM & Compound Units)";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Units";

        thead.innerHTML = `
            <tr>
                <th>Symbol</th>
                <th>Formal Name</th>
                <th>Decimal Places</th>
                <th>Type</th>
            </tr>
        `;
        try {
            const res = await fetch("/api/uoms");
            const uoms = await res.json();
            const resComp = await fetch("/api/compound_uoms");
            const compUoms = await resComp.json();

            tbody.innerHTML = "";
            uoms.forEach(u => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>${u.symbol}</strong></td>
                    <td>${u.formal_name}</td>
                    <td>${u.decimal_places}</td>
                    <td>Simple Unit</td>
                `;
                tbody.appendChild(tr);
            });
            compUoms.forEach(cu => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>${cu.major_uom} of ${cu.conversion_factor} ${cu.minor_uom}</strong></td>
                    <td>Compound Conversion</td>
                    <td>0</td>
                    <td><span style="color:#00796b; font-weight:bold;">Compound Unit</span></td>
                `;
                tbody.appendChild(tr);
            });
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan='4' style='color:red;'>Failed to load units: ${e}</td></tr>`;
        }

    } else if (currentMasterType === "godowns") {
        document.getElementById("masters-title").textContent = "Locations & Warehouses (Godowns)";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Godowns";

        thead.innerHTML = `
            <tr>
                <th>Godown / Location Name</th>
                <th>Physical Address / Details</th>
                <th>Status</th>
            </tr>
        `;
        try {
            const res = await fetch("/api/godowns");
            const godowns = await res.json();
            tbody.innerHTML = "";
            godowns.forEach(g => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>${g.name}</strong></td>
                    <td>${g.location || 'Default Company Warehouse'}</td>
                    <td><span style="color:#2e7d32;">● Active</span></td>
                `;
                tbody.appendChild(tr);
            });
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan='3' style='color:red;'>Failed to load godowns: ${e}</td></tr>`;
        }

    } else if (currentMasterType === "employees") {
        document.getElementById("masters-title").textContent = "Payroll Employee Profiles";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Employees";

        thead.innerHTML = `
            <tr>
                <th>Emp Code</th>
                <th>Employee Name</th>
                <th>Department</th>
                <th>Designation</th>
                <th style="text-align:right;">Basic Salary (₹)</th>
                <th>PAN / PF UAN</th>
            </tr>
        `;
        try {
            const res = await fetch("/api/employees");
            const emps = await res.json();
            tbody.innerHTML = "";
            emps.forEach(emp => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>${emp.emp_code}</strong></td>
                    <td>${emp.name}</td>
                    <td>${emp.department || 'Operations'}</td>
                    <td>${emp.designation || 'Executive'}</td>
                    <td style="text-align:right; font-weight:bold;">₹${(emp.basic_salary || 0).toLocaleString()}</td>
                    <td>${emp.pan || 'N/A'} / ${emp.pf_uan || 'N/A'}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan='6' style='color:red;'>Failed to load employees: ${e}</td></tr>`;
        }

    } else if (currentMasterType === "cost_centres") {
        document.getElementById("masters-title").textContent = "Cost Centres & Cost Categories";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Cost Centres";

        thead.innerHTML = `
            <tr>
                <th>Cost Centre Name</th>
                <th>Cost Category</th>
                <th>Parent Centre</th>
            </tr>
        `;
        tbody.innerHTML = "";
        state.costCentres.forEach(cc => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${cc.name}</strong></td>
                <td>${cc.category_name}</td>
                <td>${cc.parent || 'Primary'}</td>
            `;
            tbody.appendChild(tr);
        });

    } else if (currentMasterType === "tax_configs") {
        document.getElementById("masters-title").textContent = "Statutory GST Slabs & TDS Rates (Version 2026.1)";
        document.getElementById("breadcrumb").textContent = "Gateway of Tally > Masters > Tax Configurations";

        thead.innerHTML = `
            <tr>
                <th>Tax Head / Section</th>
                <th>Description</th>
                <th style="text-align:right;">Standard Rate (%)</th>
                <th>Statutory Basis</th>
            </tr>
        `;
        try {
            const res = await fetch("/api/statutory/config");
            const cfg = await res.json();
            tbody.innerHTML = "";
            
            // GST Slabs
            (cfg.gst_slabs || []).forEach(slab => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>GST ${slab.rate}%</strong></td>
                    <td>${slab.description}</td>
                    <td style="text-align:right; font-weight:bold;">${slab.rate}% (CGST ${slab.cgst}% + SGST ${slab.sgst}%)</td>
                    <td>CGST Act Sec 9 / IGST Act Sec 5</td>
                `;
                tbody.appendChild(tr);
            });

            // TDS Sections
            for (const [sec, sdata] of Object.entries(cfg.tds_sections || {})) {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>TDS ${sec}</strong></td>
                    <td>${sdata.nature_of_payment}</td>
                    <td style="text-align:right; font-weight:bold;">${sdata.rate_individual}% (Ind) / ${sdata.rate_other}% (Co)</td>
                    <td>Threshold: ₹${(sdata.threshold || 0).toLocaleString()}</td>
                `;
                tbody.appendChild(tr);
            }
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan='4' style='color:red;'>Failed to load statutory config: ${e}</td></tr>`;
        }
    }
}

// --- Toast & Notification Utility ---
function showTallyToast(msg, type = "info") {
    console.log(`[TALLY-TOAST] [${type.toUpperCase()}] ${msg}`);
    let toast = document.getElementById("tally-toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "tally-toast";
        toast.style.cssText = "position:fixed; bottom:24px; right:24px; z-index:99999; padding:10px 18px; border-radius:4px; font-weight:bold; font-size:0.85rem; box-shadow:0 4px 12px rgba(0,0,0,0.3); transition:opacity 0.3s ease;";
        document.body.appendChild(toast);
    }
    const bg = type === "success" ? "#2e7d32" : (type === "error" ? "#c62828" : (type === "warning" ? "#f57f17" : "#004d40"));
    toast.style.background = bg;
    toast.style.color = "#ffffff";
    toast.innerText = msg;
    toast.style.opacity = "1";
    toast.style.display = "block";
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.style.display = "none", 300);
    }, 3200);
}

// --- User Authentication Modal & Session Management ---
let sessionToken = sessionStorage.getItem("tally_session_token") || "";

function openAuthModal() {
    const modal = document.getElementById("auth-modal");
    if (modal) { modal.style.display = "flex"; modal.style.zIndex = "10000"; }
}

function closeAuthModal() {
    const modal = document.getElementById("auth-modal");
    if (modal) modal.style.display = "none";
}

async function submitUserLogin() {
    const u = (document.getElementById("login-username").value || "").trim();
    const p = (document.getElementById("login-password").value || "").trim();
    if (!u || !p) {
        showTallyToast("Please enter username and password", "warning");
        return;
    }
    try {
        if (pendingCompanyKey) {
            await selectStoredCompany(pendingCompanyKey, {username: u, password: p});
            document.getElementById('login-password').value = '';
            return;
        }
        const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: u, password: p })
        });
        const data = await res.json();
        if (res.ok && data.status === "success") {
            sessionToken = data.token;
            sessionStorage.setItem("tally_session_token", sessionToken);
            const userLabel = document.getElementById("nav-user-label");
            if (userLabel) userLabel.innerText = data.user.username;
            const statusLabel = document.getElementById("auth-current-user-status");
            if (statusLabel) statusLabel.innerText = `Logged in as: ${data.user.username} (${data.user.role})`;
            showTallyToast(`✓ Logged in as ${data.user.username}`, "success");
            await loadAppState();
            setTimeout(closeAuthModal, 600);
        } else {
            showTallyToast(data.error || "Login failed", "error");
        }
    } catch (e) {
        showTallyToast(`Network error: ${e}`, "error");
    }
}

async function submitUserLogout() {
    try {
        if (sessionToken) {
            await fetch("/api/auth/logout", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token: sessionToken })
            });
        }
        sessionToken = "";
        sessionStorage.removeItem("tally_session_token");
        const userLabel = document.getElementById("nav-user-label");
        if (userLabel) userLabel.innerText = "Login";
        const statusLabel = document.getElementById("auth-current-user-status");
        if (statusLabel) statusLabel.innerText = "Logged out. Using local loopback admin.";
        showTallyToast("Logged out successfully", "info");
    } catch (e) {
        showTallyToast(`Logout error: ${e}`, "error");
    }
}

// --- Master Creation Modal Handlers ---
function openMasterCreationModal(type) {
    currentMasterType = type || currentMasterType || "ledgers";
    const modal = document.getElementById("master-creation-modal");
    if (!modal) return;
    const typeSelect = document.getElementById("modal-master-type");
    if (typeSelect) typeSelect.value = currentMasterType;
    onModalMasterTypeChange();
    modal.style.display = "flex";
    setTimeout(() => {
        const nameInput = document.getElementById("modal-master-name");
        if (nameInput) nameInput.focus();
    }, 100);
}

function closeMasterCreationModal() {
    const modal = document.getElementById("master-creation-modal");
    if (modal) modal.style.display = "none";
}

function onModalMasterTypeChange() {
    const typeSelect = document.getElementById("modal-master-type");
    if (!typeSelect) return;
    const type = typeSelect.value;
    const ledgerFields = document.getElementById("modal-master-ledger-fields");
    const stockFields = document.getElementById("modal-master-stock-fields");
    const godownFields = document.getElementById("modal-master-godown-fields");
    const title = document.getElementById("master-modal-title");

    if (ledgerFields) ledgerFields.style.display = (type === "ledgers") ? "grid" : "none";
    if (stockFields) stockFields.style.display = (type === "stock_items") ? "grid" : "none";
    if (godownFields) godownFields.style.display = (type === "godowns") ? "block" : "none";

    if (title) title.innerText = `Master Creation: ${type.replace('_', ' ').toUpperCase()}`;
}

async function submitMasterCreation() {
    const typeSelect = document.getElementById("modal-master-type");
    const type = typeSelect ? typeSelect.value : currentMasterType;
    const nameInput = document.getElementById("modal-master-name");
    const name = nameInput ? nameInput.value.trim() : "";
    if (!name) {
        showTallyToast("Please enter a master name", "warning");
        return;
    }

    try {
        if (type === "ledgers") {
            const group = document.getElementById("modal-master-group").value;
            const opBal = parseFloat(document.getElementById("modal-master-opbal").value || "0");
            const opType = document.getElementById("modal-master-optype").value || "Dr";
            const res = await fetch("/api/ledgers", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, group_name: group, opening_balance: opBal, opening_type: opType })
            });
            const data = await res.json();
            if (res.ok) {
                showTallyToast(`✓ Ledger '${name}' created successfully`, "success");
                closeMasterCreationModal();
                await loadAppState();
                openMasters("ledgers");
            } else {
                showTallyToast(`Error: ${data.error}`, "error");
            }
        } else if (type === "stock_items") {
            const uom = document.getElementById("modal-master-uom").value.trim() || "Nos";
            const hsn = document.getElementById("modal-master-hsn").value.trim() || "84713010";
            const gstRate = parseFloat(document.getElementById("modal-master-gstrate").value || "18");
            const opQty = parseFloat(document.getElementById("modal-master-opqty").value || "0");
            const opRate = parseFloat(document.getElementById("modal-master-oprate").value || "0");
            const res = await fetch("/api/stock_items", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, uom, hsn_sac_code: hsn, tax_rate: gstRate, opening_qty: opQty, opening_rate: opRate })
            });
            const data = await res.json();
            if (res.ok) {
                showTallyToast(`✓ Stock Item '${name}' created successfully`, "success");
                closeMasterCreationModal();
                await loadAppState();
                openMasters("stock_items");
            } else {
                showTallyToast(`Error: ${data.error}`, "error");
            }
        } else if (type === "godowns") {
            const loc = document.getElementById("modal-master-godown-loc").value.trim() || "Default Location";
            const res = await fetch("/api/godowns", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, location: loc })
            });
            const data = await res.json();
            if (res.ok) {
                showTallyToast(`✓ Godown '${name}' created successfully`, "success");
                closeMasterCreationModal();
                await loadAppState();
                openMasters("godowns");
            } else {
                showTallyToast(`Error: ${data.error}`, "error");
            }
        } else {
            createMasterPromptFallback();
        }
    } catch (e) {
        showTallyToast(`Failed to create master: ${e}`, "error");
    }
}

async function createMasterPrompt() {
    openMasterCreationModal(currentMasterType);
}

async function createMasterPromptFallback() {
    if (currentMasterType === "ledgers") {
        const name = prompt("Enter Ledger Name:");
        if (!name) return;
        const group = prompt("Enter Under Group (e.g. Sundry Debtors, Sundry Creditors, Indirect Expenses, Bank Accounts):", "Sundry Debtors");
        if (!group) return;
        const opBal = parseFloat(prompt("Opening Balance (₹):", "0") || "0");
        const opType = prompt("Balance Type (Dr / Cr):", "Dr") || "Dr";

        const res = await fetch("/api/ledgers", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, group_name: group, opening_balance: opBal, opening_type: opType })
        });
        if (res.ok) {
            alert("✓ Ledger Created Successfully!");
            await loadAppState();
            openMasters("ledgers");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }

    } else if (currentMasterType === "groups") {
        const name = prompt("Enter Group Name:");
        if (!name) return;
        const parent = prompt("Enter Parent Group (Primary / Current Assets / Current Liabilities etc.):", "Primary") || "Primary";
        const isRev = confirm("Is this a Revenue Group (affects Profit & Loss)? Click OK for Yes, Cancel for Balance Sheet.");

        const res = await fetch("/api/groups", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, parent: parent, is_revenue: isRev })
        });
        if (res.ok) {
            alert("✓ Group Created Successfully!");
            openMasters("groups");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }

    } else if (currentMasterType === "stock_items") {
        const name = prompt("Enter Stock Item Name:");
        if (!name) return;
        const uom = prompt("Unit of Measure (Nos, Pcs, Box, Kg):", "Nos") || "Nos";
        const hsn = prompt("HSN Code:", "84433100") || "84433100";
        const gstRate = parseFloat(prompt("GST Rate % (e.g. 18, 12, 5):", "18") || "18");
        const opQty = parseFloat(prompt("Opening Quantity:", "0") || "0");
        const opRate = parseFloat(prompt("Opening Rate (₹):", "0") || "0");

        const res = await fetch("/api/stock_items", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, group_name: "Primary", uom_symbol: uom, hsn_code: hsn, gst_rate: gstRate, opening_qty: opQty, opening_rate: opRate })
        });
        if (res.ok) {
            alert("✓ Stock Item Created Successfully!");
            await loadAppState();
            openMasters("stock_items");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }

    } else if (currentMasterType === "units") {
        const sym = prompt("Enter Unit Symbol (e.g. Mtr, Box, Ltr, Pcs):");
        if (!sym) return;
        const formal = prompt("Enter Formal Name (e.g. Metres, Boxes, Litres):", sym) || sym;
        const dec = parseInt(prompt("Number of decimal places (0-4):", "0") || "0");

        const res = await fetch("/api/uoms", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: sym, formal_name: formal, decimal_places: dec })
        });
        if (res.ok) {
            alert("✓ Unit of Measure Created Successfully!");
            openMasters("units");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }

    } else if (currentMasterType === "godowns") {
        const name = prompt("Enter Godown / Warehouse Name (e.g. Warehouse North):");
        if (!name) return;
        const loc = prompt("Enter Warehouse Location / Address:", "Plot 42, Industrial Area") || "Default Location";

        const res = await fetch("/api/godowns", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, location: loc })
        });
        if (res.ok) {
            alert("✓ Godown / Warehouse Created Successfully!");
            openMasters("godowns");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }

    } else if (currentMasterType === "employees") {
        const code = prompt("Enter Employee Code (e.g. EMP-003):");
        if (!code) return;
        const name = prompt("Enter Employee Full Name:");
        if (!name) return;
        const dept = prompt("Department (Operations / Accounts / Sales / HR):", "Operations") || "Operations";
        const desig = prompt("Designation:", "Executive") || "Executive";
        const salary = parseFloat(prompt("Basic Monthly Salary (₹):", "35000") || "35000");
        const pan = prompt("PAN Number (optional):", "") || "";

        const res = await fetch("/api/employees", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ emp_code: code, name: name, department: dept, designation: desig, basic_salary: salary, pan: pan })
        });
        if (res.ok) {
            alert("✓ Employee Created Successfully!");
            openMasters("employees");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }

    } else if (currentMasterType === "cost_centres") {
        const name = prompt("Enter Cost Centre Name (e.g. Bengaluru Project, R&D Dept):");
        if (!name) return;
        const cat = prompt("Cost Category (Departments / Projects):", "Departments") || "Departments";

        const res = await fetch("/api/cost_centres", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, category_name: cat, parent: "Primary" })
        });
        if (res.ok) {
            alert("✓ Cost Centre Created Successfully!");
            await loadAppState();
            openMasters("cost_centres");
        } else {
            const data = await res.json();
            alert(`Error: ${data.error}`);
        }
    } else {
        alert("Statutory GST & TDS configurations are managed according to statutory notifications.");
    }
}

// -------------------------------------------------------------------------
// TDL Studio (Live Code Hot-Reload & Syntax Validation)
// -------------------------------------------------------------------------
async function openTDLStudio() {
    if (_dashboardLoadTimeout) {
        clearTimeout(_dashboardLoadTimeout);
        _dashboardLoadTimeout = null;
    }
    state.currentScreen = "tdl_studio";

    const dash = document.getElementById("dashboard-screen");
    if (dash) dash.style.display = "none";
    document.getElementById("gateway-box").style.display = "none";
    document.getElementById("report-screen").style.display = "none";
    document.getElementById("voucher-screen").style.display = "none";
    document.getElementById("masters-screen").style.display = "none";
    document.getElementById("tdl-studio-screen").style.display = "flex";

    document.getElementById("breadcrumb").textContent = "Gateway of Tally > TDL Studio";

    const editor = document.getElementById("tdl-editor");
    if (!editor.value.trim()) {
        try {
            const res = await fetch("/api/tdl/code");
            const data = await res.json();
            editor.value = data.code || "";
        } catch (e) {
            editor.value = ";; Write custom TDL definitions here";
        }
    }
}

async function validateTDLCode(showMessage = false) {
    const editor = document.getElementById("tdl-editor");
    const diagBox = document.getElementById("tdl-diagnostics");
    const code = editor.value;

    try {
        const res = await fetch("/api/tdl/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: code })
        });
        const data = await res.json();
        
        if (data.diagnostics && data.diagnostics.length > 0) {
            diagBox.style.display = "block";
            diagBox.innerHTML = data.diagnostics.map(d => `
                <div style="color:${d.severity === 'error' ? '#c62828' : '#e65100'}; margin-bottom:3px;">
                    <strong>Line ${d.line}:</strong> ${d.message} <span style="color:#666;">(<code>${d.context}</code>)</span>
                </div>
            `).join("");
            if (showMessage) alert(`Found ${data.diagnostics.length} issue(s) in TDL code! Check the panel below.`);
            return false;
        } else {
            diagBox.style.display = "none";
            diagBox.innerHTML = "";
            if (showMessage) alert("✓ TDL Syntax is 100% Valid!");
            return true;
        }
    } catch (err) {
        console.error("TDL validation error:", err);
        return false;
    }
}

async function compileAndApplyTDL() {
    const editor = document.getElementById("tdl-editor");
    const code = editor.value;

    const isValid = await validateTDLCode(false);
    if (!isValid) {
        if (!confirm("TDL syntax contains issues. Do you still want to proceed with compilation?")) {
            return;
        }
    }

    try {
        const res = await fetch("/api/tdl/load", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: code })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`✓ TDL Compiled & Applied Successfully!\nTotal Active Definitions: ${data.total_definitions}`);
            await loadAppState();
        } else {
            alert(`TDL Compilation Error: ${data.error}`);
        }
    } catch (err) {
        alert(`Compilation request error: ${err}`);
    }
}

// -------------------------------------------------------------------------
// TDL Studio Multi-Tab & Offline Extension Manager
// -------------------------------------------------------------------------
function switchTDLTab(tab) {
    const tabs = ['editor', 'manager', 'compat'];
    tabs.forEach(t => {
        const pane = document.getElementById(`tdl-pane-${t}`);
        const btn = document.getElementById(`tdl-tab-btn-${t}`);
        if (pane) pane.style.display = (t === tab) ? "flex" : "none";
        if (btn) btn.style.background = (t === tab) ? "#00796b" : "#004d40";
    });

    if (tab === 'manager') loadTDLExtensionsList();
    if (tab === 'compat') loadTDLCompatibility();
}

async function loadTDLExtensionsList() {
    const tbody = document.getElementById("tdl-extensions-table-body");
    if (!tbody) return;
    tbody.innerHTML = "<tr><td colspan='6' style='text-align:center; padding:12px;'>Loading installed extensions...</td></tr>";

    try {
        const res = await fetch("/api/tdl/extensions");
        const list = await res.json();
        tbody.innerHTML = "";

        if (!list || list.length === 0) {
            tbody.innerHTML = "<tr><td colspan='6' style='text-align:center; padding:12px;'>No TDL extensions found.</td></tr>";
            return;
        }

        list.forEach(ext => {
            const tr = document.createElement("tr");
            const isProtected = ext.protected;
            const statusColor = ext.status === "Active" ? "#2e7d32" : (ext.status === "Disabled" ? "#f57f17" : "#c62828");

            tr.innerHTML = `
                <td><strong>${ext.filename}</strong> ${isProtected ? '<span style="color:#004d40; font-size:0.7rem; font-weight:bold;">[CORE]</span>' : ''}</td>
                <td>${ext.description || 'Custom TDL Extension'}</td>
                <td style="text-align:center;"><span style="color:${statusColor}; font-weight:bold;">● ${ext.status}</span></td>
                <td style="text-align:right; font-weight:bold;">${ext.definitions_count}</td>
                <td style="text-align:right;">${ext.syntax_errors === 0 ? '<span style="color:#2e7d32;">0 Errors (Clean)</span>' : `<span style="color:#c62828; font-weight:bold;">${ext.syntax_errors} Error(s)</span>`}</td>
                <td style="text-align:center;">
                    <button class="btn-tally-primary" style="padding:1px 6px; font-size:0.72rem; background:#00796b;" onclick="inspectTDLExtension('${ext.filename}')">Inspect AST</button>
                    ${isProtected ? '' : `
                        <button class="btn-tally-primary" style="padding:1px 6px; font-size:0.72rem; background:${ext.enabled ? '#f57f17' : '#2e7d32'};" onclick="toggleTDLExtension('${ext.filename}', ${!ext.enabled})">
                            ${ext.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button class="btn-tally-primary" style="padding:1px 6px; font-size:0.72rem; background:#c62828;" onclick="removeTDLExtension('${ext.filename}')">Remove</button>
                    `}
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan='6' style='color:red;'>Failed to load extensions: ${e}</td></tr>`;
    }
}

async function toggleTDLExtension(filename, enabled) {
    try {
        const res = await fetch("/api/tdl/extensions/toggle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: filename, enabled: enabled })
        });
        if (res.ok) {
            await loadTDLExtensionsList();
            await loadAppState();
        } else {
            const d = await res.json();
            alert(`Error toggling extension: ${d.error}`);
        }
    } catch (e) {
        alert(`Request failed: ${e}`);
    }
}

async function inspectTDLExtension(filename) {
    const box = document.getElementById("tdl-extension-inspect-view");
    if (!box) return;
    box.textContent = `Inspecting AST hierarchy for ${filename}...`;

    try {
        const res = await fetch(`/api/tdl/extensions/inspect?filename=${encodeURIComponent(filename)}`);
        const data = await res.json();
        if (res.ok) {
            box.textContent = JSON.stringify(data, null, 2);
        } else {
            box.textContent = `Error: ${data.error}`;
        }
    } catch (e) {
        box.textContent = `Inspect failed: ${e}`;
    }
}

async function removeTDLExtension(filename) {
    if (!confirm(`Are you sure you want to remove extension '${filename}'? This cannot be undone.`)) {
        return;
    }
    try {
        const res = await fetch("/api/tdl/extensions/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: filename })
        });
        const d = await res.json();
        if (res.ok) {
            alert(`✓ Extension '${filename}' removed safely.`);
            await loadTDLExtensionsList();
            await loadAppState();
        } else {
            alert(`Removal error: ${d.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e}`);
    }
}

async function reloadTDLExtensions() {
    try {
        const res = await fetch("/api/tdl/extensions/reload", { method: "POST" });
        const d = await res.json();
        if (res.ok) {
            alert(`✓ All TDL Extensions Reloaded Successfully!\nTotal Active Definitions: ${d.total_definitions}`);
            await loadTDLExtensionsList();
            await loadAppState();
        } else {
            alert(`Reload Error: ${d.error}`);
        }
    } catch (e) {
        alert(`Request failed: ${e}`);
    }
}

async function openImportTDLModal() {
    const fname = prompt("Enter TDL File Name (e.g. branch_reporting.tdl):", "custom_branch_reports.tdl");
    if (!fname) return;
    const desc = prompt("Enter Extension Description:", "Custom Branch Reporting & Audit Extension") || "";
    const sampleCode = `[#Menu: Gateway of Tally]\nAdd : Item : "Branch Analysis" : Display : TrialBalance\n`;
    const code = prompt("Enter or paste TDL Code definition string:", sampleCode);
    if (!code) return;

    try {
        const res = await fetch("/api/tdl/extensions/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: fname, content: code, description: desc })
        });
        const d = await res.json();
        if (res.ok) {
            alert(`✓ Extension '${fname}' Imported Successfully! Valid: ${d.valid}`);
            await loadTDLExtensionsList();
            await loadAppState();
        } else {
            alert(`Import error: ${d.error}`);
        }
    } catch (e) {
        alert(`Failed to import: ${e}`);
    }
}

async function loadTDLCompatibility() {
    const typesEl = document.getElementById("compat-supported-types");
    const fnsEl = document.getElementById("compat-supported-functions");
    const unsuppEl = document.getElementById("compat-unsupported-features");

    try {
        const res = await fetch("/api/tdl/compatibility");
        const data = await res.json();

        if (typesEl) {
            typesEl.innerHTML = (data.supported_definition_types || []).map(t => 
                `<span style="display:inline-block; background:#e0f2f1; color:#004d40; padding:2px 6px; margin:2px; border-radius:3px; font-weight:bold;">[${t}]</span>`
            ).join(" ");
        }

        if (fnsEl) {
            fnsEl.innerHTML = (data.supported_functions || []).map(fn => 
                `<div>• <code>${fn}</code></div>`
            ).join("");
        }

        if (unsuppEl) {
            unsuppEl.innerHTML = (data.unsupported_features || []).map(u => `
                <div style="margin-bottom:8px;">
                    <strong style="color:#c62828;">${u.feature}</strong>
                    <div style="font-size:0.75rem; color:#666;">${u.reason}</div>
                </div>
            `).join("");
        }
    } catch (e) {
        console.error("Failed to load compatibility report:", e);
    }
}

// -------------------------------------------------------------------------
// Bottom Calculator (Ctrl+N / Ctrl+M)
// -------------------------------------------------------------------------
function toggleCalculator(forceState) {
    const drawer = document.getElementById("calc-drawer");
    const shouldOpen = forceState !== undefined ? forceState : drawer.style.display !== "flex";

    drawer.style.display = shouldOpen ? "flex" : "none";
    if (shouldOpen) {
        document.getElementById("calc-input").focus();
    }
}

function handleCalcKeyDown(e) {
    if (e.key === "Enter") {
        e.preventDefault();
        const input = document.getElementById("calc-input");
        const expr = input.value.trim();
        if (!expr) return;

        try {
            if (/^[0-9+\-*/().\s%]+$/.test(expr)) {
                const result = Function(`'use strict'; return (${expr})`)();
                const formatted = `${expr} = ${result.toLocaleString()}`;
                state.calcHistory.push(formatted);

                const histEl = document.getElementById("calc-history");
                histEl.innerHTML = state.calcHistory.slice(-4).map(h => `<div>${h}</div>`).join("");
                input.value = result;
                input.select();
            } else {
                alert("Only numbers and +, -, *, /, %, () are permitted.");
            }
        } catch (err) {
            alert(`Invalid Expression: ${err}`);
        }
    }
}

// -------------------------------------------------------------------------
// Alt+G "Go To" Smart Search Modal
// -------------------------------------------------------------------------
function openGoToModal() {
    document.getElementById("goto-modal").style.display = "flex";
    const input = document.getElementById("goto-search-input");
    input.value = "";
    filterGoToItems();
    input.focus();
}

function closeGoToModal() {
    document.getElementById("goto-modal").style.display = "none";
}

async function filterGoToItems() {
    const q = document.getElementById("goto-search-input").value.trim();
    const list = document.getElementById("goto-results-list");
    list.innerHTML = "";

    try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(q || 'a')}`);
        const data = await res.json();
        const results = data.results || [];

        if (results.length === 0) {
            list.innerHTML = '<div style="padding:10px; color:#777; font-size:0.85rem;">No matching reports, accounts, or items.</div>';
            return;
        }

        results.forEach((item, idx) => {
            const div = document.createElement("div");
            div.style.padding = "6px 12px";
            div.style.cursor = "pointer";
            div.style.borderBottom = "1px solid #e0f2f1";
            div.style.display = "flex";
            div.style.justifyContent = "space-between";
            div.className = idx === 0 ? "goto-row active" : "goto-row";
            div.innerHTML = `<span><strong>${item.label}</strong></span><span style="color:#777; font-size:0.75rem;">${item.category}</span>`;
            div.onclick = () => {
                closeGoToModal();
                if (item.target.startsWith("GeneralLedger:")) {
                    const lName = item.target.split(":")[1];
                    state.selectedLedgerName = lName;
                    openReport("GeneralLedger", `General Ledger (${lName})`);
                } else if (item.target === "TDLStudio") {
                    openTDLStudio();
                } else if (item.category === "Voucher") {
                    openReport("DayBook", "Day Book");
                } else {
                    openReport(item.target, item.label);
                }
            };
            list.appendChild(div);
        });
    } catch (err) {
        console.error("Search error:", err);
    }
}

function handleGoToKey(e) {
    if (e.key === "Enter") {
        const first = document.querySelector(".goto-row");
        if (first) first.click();
    }
}

// -------------------------------------------------------------------------
// Helper Utilities: Print, Export, Prompts
// -------------------------------------------------------------------------
function printReport() {
    window.print();
}

function exportDataCSV() {
    const table = document.getElementById("report-data-table");
    if (!table || state.currentScreen !== "report") {
        alert("Open a report first to export its data to CSV!");
        return;
    }
    let csv = [];
    for (let row of table.rows) {
        let cols = Array.from(row.cells).map(c => `"${c.innerText.replace(/"/g, '""')}"`);
        csv.push(cols.join(","));
    }
    const blob = new Blob([csv.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.currentReportName || 'Tally_Report'}_${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
}

function openDatePrompt() {
    const newDate = prompt("Enter Current Date (YYYY-MM-DD):", new Date().toISOString().split("T")[0]);
    if (newDate) {
        document.getElementById("current-date-val").textContent = newDate;
    }
}

function openCompanyInfo() {
    alert(`Current Company: ${state.company.name || 'Demo Enterprise Ltd.'}\nGSTIN: ${state.company.gstin || '29AABCT1332L1Z5'}\nState: ${state.company.state || 'Karnataka'}\nPeriod: 1-Apr-2026 to 31-Mar-2027\nMode: 100% Offline Standalone`);
}

function openFeatures() {
    alert("F11: Company Features:\n\n✓ Maintain Accounts: YES\n✓ Enable Cost Centres: YES\n✓ Maintain Inventory: YES\n✓ Enable Goods and Services Tax (GST): YES\n✓ Enable Bill-by-Bill: YES\n✓ Multi-Currency: ₹, $, €\n✓ Payroll Management: YES\n✓ Statutory Edit Log: COMPLIANT\n✓ TDL Compatibility: Full Active");
}

// -------------------------------------------------------------------------
// PHASE 5: Company Management Modal (Tabs: Select, Alter, Create, Security, Users)
// -------------------------------------------------------------------------
async function openCompanyModal() {
    const modal = document.getElementById("company-modal");
    if (!modal) return;
    if (companyReady && state.currentScreen === 'voucher' && !confirm('Discard the unfinished voucher and choose another company?')) return;
    modal.style.display = "flex";
    switchCompanyTab('select');
}

function closeCompanyModal() {
    if (!companyReady) return;
    const modal = document.getElementById("company-modal");
    if (modal) modal.style.display = "none";
}

function switchCompanyTab(tab) {
    if (!companyReady && !['select', 'create'].includes(tab)) return;
    const tabs = ['select', 'alter', 'create', 'security', 'users', 'group'];
    tabs.forEach(t => {
        const pane = document.getElementById(`comp-pane-${t}`);
        const btn = document.getElementById(`comp-tab-${t === 'security' ? 'sec' : t}-btn`);
        if (pane) pane.style.display = (t === tab) ? "block" : "none";
        if (btn) {
            btn.style.fontWeight = (t === tab) ? "bold" : "normal";
            btn.style.borderBottom = (t === tab) ? "2px solid #004d40" : "none";
            btn.style.color = (t === tab) ? "#004d40" : "#555";
        }
    });

    if (tab === 'select') loadCompaniesList();
    if (tab === 'alter') loadAlterCompanyData();
    if (tab === 'users') loadUsersList();
    if (tab === 'group') loadGroupCompanyData();
}

async function loadGroupCompanyData() {
    const container = document.getElementById("group-comp-members-list");
    if (!container) return;
    container.innerHTML = "Loading companies...";
    try {
        const res = await fetch("/api/companies");
        const list = await res.json();
        container.innerHTML = "";
        list.forEach(c => {
            const label = document.createElement("label");
            label.style.display = "block";
            label.style.fontSize = "0.82rem";
            label.style.marginBottom = "4px";
            label.innerHTML = `<input type="checkbox" class="group-member-checkbox" value="${c.id}" ${c.is_active ? 'checked' : ''}> <strong>${c.name}</strong> <span style="color:#666;">(ID ${c.id})</span>`;
            container.appendChild(label);
        });
    } catch (err) {
        container.innerHTML = `Error: ${err}`;
    }
}

async function submitCreateGroupCompany() {
    const name = document.getElementById("group-comp-name").value.trim();
    if (!name) {
        alert("Please enter a Group Company name!");
        return;
    }
    const checkboxes = document.querySelectorAll(".group-member-checkbox:checked");
    const memberIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    if (memberIds.length < 2) {
        alert("Please select at least 2 member companies to consolidate!");
        return;
    }

    try {
        const res = await fetch("/api/companies/consolidate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, member_ids: memberIds })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`✓ Consolidated Group Company '${name}' Created Successfully!`);
            document.getElementById("group-comp-name").value = "";
            await loadAppState();
            switchCompanyTab("select");
        } else {
            alert(`Consolidation Error: ${data.error}`);
        }
    } catch (err) {
        alert(`Failed to create group company: ${err}`);
    }
}

async function storageRequest(action, body) {
    const response = await fetch('/api/storage/' + action, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || 'Company operation failed'); error.status = response.status; throw error; }
    return result;
}

async function changeCompanyDataPath() {
    try {
        await storageRequest('path', {data_path: document.getElementById('company-data-path').value});
        await loadCompaniesList();
    } catch (err) { document.getElementById('company-storage-status').textContent = err.message; }
}

async function selectStoredCompany(key, credentials = {}) {
    try {
        let result;
        try { result = await storageRequest('select', {storage_key: key, ...credentials}); }
        catch (err) {
            if (err.status !== 401) throw err;
            pendingCompanyKey = key;
            openAuthModal();
            document.getElementById('auth-current-user-status').textContent = 'Sign in to open the selected company';
            return;
        }
        pendingCompanyKey = null;
        closeAuthModal();
        companyContext = result.company_context;
        sessionStorage.setItem('tf_company_context', companyContext);
        sessionToken = result.token || '';
        if (sessionToken) sessionStorage.setItem('tally_session_token', sessionToken);
        else sessionStorage.removeItem('tally_session_token');
        await loadAppState();
        companyReady = true;
        goBackToGateway();
        closeCompanyModal();
    } catch (err) { document.getElementById('company-storage-status').textContent = err.message; }
}

async function loadCompaniesList() {
    const container = document.getElementById('company-list-container');
    if (!container) return;
    container.textContent = 'Loading companies…';
    try {
        const res = await fetch('/api/storage', {cache: 'no-store'});
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
        document.getElementById('company-data-path').value = data.data_path;
        if (!Array.isArray(data.native_companies)) {
            throw new Error('The running backend is outdated and cannot detect native Tally folders. Fully stop and restart TallyForge using start_tally.command, then reload this page. Refreshing the browser alone does not update the backend.');
        }
        document.getElementById('company-storage-status').textContent = data.errors.map(e => e.folder + ': ' + e.error).join('; ');
        container.replaceChildren();
        if (!data.companies.length && !data.native_companies.length) container.textContent = 'No supported companies or native Tally folders were found here.';
        data.native_companies.forEach(c => {
            const row=document.createElement('button');row.type='button';row.className='tally-input';row.style.cssText='display:block;width:100%;text-align:left;margin:4px 0;cursor:pointer;white-space:normal';
            row.textContent=c.company_number+' · '+c.name+' · '+c.format+' — Open company preview';row.addEventListener('click',()=>inspectNativeCompany(c.storage_key,true));container.appendChild(row);
        });
        data.companies.forEach(c => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'tally-input';
            row.style.cssText = 'display:block;width:100%;text-align:left;margin:4px 0;cursor:pointer;white-space:normal';
            row.textContent = `${c.company_number === 'legacy' ? 'Legacy / ' + c.id : c.company_number} · ${c.name}${c.is_active ? ' [ACTIVE]' : ''}\nFY ${c.financial_year_from} to ${c.financial_year_to} · Books ${c.books_from} · Last voucher ${c.last_voucher_date || 'None'} · GSTIN ${c.gstin || 'Unregistered'}`;
            row.addEventListener('click', () => selectStoredCompany(c.storage_key));
            container.appendChild(row);
        });
    } catch (err) { container.textContent = err.message; }
}

async function switchCompany(cid) {
    try {
        const res = await fetch("/api/companies/switch", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ company_id: cid })
        });
        if (res.ok) {
            closeCompanyModal();
            await loadAppState();
            alert(`Switched to Company ID ${cid}`);
        } else {
            alert("Error switching company");
        }
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

async function loadAlterCompanyData() {
    try {
        const res = await fetch("/api/companies");
        const list = await res.json();
        const active = list.find(c => c.is_active);
        if (active) {
            const nameEl = document.getElementById("alter-comp-name");
            const gstinEl = document.getElementById("alter-comp-gstin");
            const stateEl = document.getElementById("alter-comp-state");
            const codeEl = document.getElementById("alter-comp-code");
            const addrEl = document.getElementById("alter-comp-address");
            const pinEl = document.getElementById("alter-comp-pincode");
            const fyFromEl = document.getElementById("alter-comp-fy-from");
            const fyToEl = document.getElementById("alter-comp-fy-to");

            if (nameEl) nameEl.value = active.name || "";
            if (gstinEl) gstinEl.value = active.gstin || "";
            if (stateEl) stateEl.value = active.state || "";
            if (codeEl) codeEl.value = active.state_code || "";
            if (addrEl) addrEl.value = active.address || "";
            if (pinEl) pinEl.value = active.pincode || "";
            if (fyFromEl) fyFromEl.value = active.financial_year_from || "2026-04-01";
            if (fyToEl) fyToEl.value = active.financial_year_to || "2027-03-31";
        }
    } catch (e) {
        console.error("Error loading company for alter:", e);
    }
}

async function submitAlterCompany() {
    try {
        const res = await fetch("/api/companies");
        const list = await res.json();
        const active = list.find(c => c.is_active);
        if (!active) { alert("No active company found!"); return; }

        const payload = {
            company_id: active.id,
            name: document.getElementById("alter-comp-name").value.trim(),
            gstin: document.getElementById("alter-comp-gstin").value.trim(),
            state: document.getElementById("alter-comp-state").value.trim(),
            state_code: document.getElementById("alter-comp-code").value.trim(),
            address: document.getElementById("alter-comp-address").value.trim(),
            pincode: document.getElementById("alter-comp-pincode").value.trim(),
            fy_from: document.getElementById("alter-comp-fy-from").value,
            fy_to: document.getElementById("alter-comp-fy-to").value
        };

        const postRes = await fetch("/api/companies/alter", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await postRes.json();
        if (postRes.ok) {
            alert("✓ Company Information Altered Successfully!");
            closeCompanyModal();
            loadAppState();
        } else {
            alert(`Error altering company: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

async function shutActiveCompany() {
    try {
        const res = await fetch("/api/companies");
        const list = await res.json();
        const active = list.find(c => c.is_active);
        if (!active) { alert("No active company selected!"); return; }
        if (!confirm(`Are you sure you want to shut company '${active.name}'?`)) return;

        const postRes = await fetch("/api/companies/shut", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ company_id: active.id })
        });
        const data = await postRes.json();
        if (postRes.ok) {
            companyReady = false;
            companyContext = null;
            sessionToken = '';
            sessionStorage.removeItem('tf_company_context');
            sessionStorage.removeItem('tally_session_token');
            state.company = null;
            document.getElementById('side-company-name').textContent = 'Select Company';
            openCompanyModal();
        } else {
            alert(`Error: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

async function deleteActiveCompany() {
    try {
        const res = await fetch("/api/companies");
        const list = await res.json();
        const active = list.find(c => c.is_active);
        if (!active) { alert("No active company selected!"); return; }
        if (!confirm(`CAUTION: Are you sure you want to permanently delete '${active.name}' and all its vouchers, ledgers, and items?`)) return;

        const postRes = await fetch("/api/companies/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ company_id: active.id })
        });
        const data = await postRes.json();
        if (postRes.ok) {
            alert(data.message || "Company removed from the company list.");
            companyReady = false;
            companyContext = null;
            sessionStorage.removeItem('tf_company_context');
            sessionStorage.removeItem('tally_session_token');
            state.company = null;
            openCompanyModal();
        } else {
            alert(`Error deleting company: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

async function submitCreateCompany() {
    const name = document.getElementById("new-comp-name").value.trim();
    const gstin = document.getElementById("new-comp-gstin").value.trim();
    const stateVal = document.getElementById("new-comp-state").value.trim();
    const code = document.getElementById("new-comp-code").value.trim();

    if (!name) {
        alert("Company Name is required!");
        return;
    }

    try {
        const data = await storageRequest('create', {
            name, gstin, state: stateVal, state_code: code,
            address: document.getElementById('new-comp-address').value.trim(),
            pincode: document.getElementById('new-comp-pin').value.trim(),
            financial_year_from: document.getElementById('new-comp-fy-from').value,
            financial_year_to: document.getElementById('new-comp-fy-to').value,
            books_from: document.getElementById('new-comp-books').value,
            currency_symbol: document.getElementById('new-comp-currency').value.trim() || '₹'
        });
        await selectStoredCompany(data.storage_key);
        document.getElementById('new-comp-name').value = '';
        document.getElementById('new-comp-gstin').value = '';
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

async function submitCompanySecurity() {
    try {
        const res = await fetch("/api/companies");
        const list = await res.json();
        const active = list.find(c => c.is_active);
        if (!active) { alert("No active company selected!"); return; }

        const enabled = document.getElementById("comp-sec-enabled").checked;
        const pwd = document.getElementById("comp-sec-password").value;

        const admin_username = document.getElementById('comp-admin-username').value;
        const admin_password = document.getElementById('comp-admin-password').value;
        const postRes = await fetch("/api/companies/security", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                company_id: active.id,
                security_enabled: enabled,
                vault_password: pwd, admin_username, admin_password
            })
        });
        const data = await postRes.json();
        if (postRes.ok) {
            alert("✓ Company access settings saved. Sign in to continue. Database encryption is a separate capability.");
            closeCompanyModal();
            loadAppState();
        } else {
            alert(`Error updating security: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

async function loadUsersList() {
    const container = document.getElementById("user-accounts-list");
    if (!container) return;
    container.innerHTML = "Loading users...";
    try {
        const res = await fetch("/api/users");
        const users = await res.json();
        if (!res.ok) {
            container.innerHTML = `<span style="color:red;">${users.error || 'Access Denied'}</span>`;
            return;
        }
        if (users.length === 0) {
            container.innerHTML = "<span>No users found.</span>";
            return;
        }
        let html = '<table style="width:100%; border-collapse:collapse; font-size:0.75rem;">';
        html += '<tr style="background:#f0f4f8; text-align:left;"><th>User</th><th>Role</th><th>Status</th><th>Action</th></tr>';
        users.forEach(u => {
            html += `<tr style="border-bottom:1px solid #eee;">
                <td><strong>${u.username}</strong></td>
                <td>${u.role}</td>
                <td><span style="color:${u.is_active ? 'green' : 'red'};">${u.is_active ? 'Active' : 'Disabled'}</span></td>
                <td>
                    ${u.username === 'admin' ? '<span style="color:#888;">Locked</span>' : `<button class="btn-tally-primary" style="padding:1px 6px; font-size:0.7rem; background:#c62828;" onclick="deleteUserAccount(${u.id})">Delete</button>`}
                </td>
            </tr>`;
        });
        html += '</table>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<span style="color:red;">Error loading users: ${e.message}</span>`;
    }
}

async function submitCreateUser() {
    const uname = document.getElementById("new-user-name").value.trim();
    const pwd = document.getElementById("new-user-pwd").value;
    const role = document.getElementById("new-user-role").value;

    if (!uname || !pwd) {
        alert("Username and Password are required!");
        return;
    }

    try {
        const res = await fetch("/api/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: uname, password: pwd, role: role })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`User '${uname}' created successfully!`);
            document.getElementById("new-user-name").value = "";
            document.getElementById("new-user-pwd").value = "";
            loadUsersList();
        } else {
            alert(`Error: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

async function deleteUserAccount(userId) {
    if (!confirm("Are you sure you want to delete this user?")) return;
    try {
        const res = await fetch("/api/users/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: userId })
        });
        const data = await res.json();
        if (res.ok) {
            alert("User deleted successfully.");
            loadUsersList();
        } else {
            alert(`Error: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

// -------------------------------------------------------------------------
// PHASE 5: Data Management, Verification, Repair & Audit History
// -------------------------------------------------------------------------
function openDataModal() {
    const modal = document.getElementById("data-modal");
    if (modal) modal.style.display = "flex";
}

function closeDataModal() {
    const modal = document.getElementById("data-modal");
    if (modal) modal.style.display = "none";
}

async function runDataVerification() {
    const resultsDiv = document.getElementById("data-verify-results");
    if (!resultsDiv) return;
    resultsDiv.style.display = "block";
    resultsDiv.innerHTML = "Executing SQLite PRAGMA integrity check, foreign keys, equilibrium, and audit chain...";

    try {
        const res = await fetch("/api/data/verify");
        const data = await res.json();
        if (res.ok) {
            let statusColor = data.status === "HEALTHY" ? "#2e7d32" : "#c62828";
            let html = `<div style="font-weight:bold; color:${statusColor}; margin-bottom:4px;">Status: ${data.status}</div>`;
            html += `<div>• SQLite Integrity: <strong>${data.sqlite_integrity}</strong></div>`;
            html += `<div>• Foreign Key Violations: <strong>${data.foreign_keys}</strong></div>`;
            html += `<div>• Double-Entry Balance Diff: <strong>₹${(data.double_entry_diff || 0).toFixed(2)}</strong></div>`;
            html += `<div>• Audit Chain Tamper Check: <strong>${data.audit_tampered ? 'TAMPERED' : 'VALID & VERIFIED'}</strong></div>`;
            resultsDiv.innerHTML = html;
        } else {
            resultsDiv.innerHTML = `<span style="color:red;">Verification error: ${data.error}</span>`;
        }
    } catch (e) {
        resultsDiv.innerHTML = `<span style="color:red;">Error: ${e.message}</span>`;
    }
}

async function runDataRepair() {
    if (!confirm("Run Data Repair and Reindex? A safety snapshot backup will be created automatically before maintenance.")) return;
    const resultsDiv = document.getElementById("data-verify-results");
    if (resultsDiv) {
        resultsDiv.style.display = "block";
        resultsDiv.innerHTML = "Creating safety snapshot and executing VACUUM + REINDEX...";
    }
    try {
        const res = await fetch("/api/data/repair", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            alert(`✓ Database Repaired Successfully!\nSafety Snapshot: ${data.safety_backup}\nIntegrity Post-Repair: ${data.integrity}`);
            if (resultsDiv) {
                resultsDiv.innerHTML = `<span style="color:#2e7d32; font-weight:bold;">Repaired & Reindexed! Integrity: ${data.integrity}</span>`;
            }
        } else {
            alert(`Repair failed: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed: ${e.message}`);
    }
}

async function openAuditHistoryModal() {
    try {
        const res = await fetch("/api/system/audit_history");
        const logs = await res.json();
        if (!res.ok) {
            alert(`Cannot view audit history: ${logs.error || 'Access Denied'}`);
            return;
        }
        let txt = `MCA Rule 3(1) Statutory Audit Trail (${logs.length} events logged):\n\n`;
        logs.slice(0, 15).forEach(log => {
            txt += `[${log.timestamp}] [${log.action}] ${log.entity_type} #${log.entity_id || ''} by ${log.user_name || 'System'}\n`;
        });
        if (logs.length > 15) txt += `\n... and ${logs.length - 15} more records.`;
        alert(txt);
    } catch (e) {
        alert(`Failed to fetch audit history: ${e.message}`);
    }
}

async function promptCloseFY() {
    const curEnd = prompt("Current Financial Year End (YYYY-MM-DD):", "2027-03-31");
    if (!curEnd) return;
    const nextStart = prompt("New Financial Year Start (YYYY-MM-DD):", "2027-04-01");
    if (!nextStart) return;

    try {
        const res = await fetch("/api/company/close_fy", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ current_fy_end: curEnd, next_fy_start: nextStart })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`✓ Financial Year Closed Successfully!\n\nTransferred Net Profit: ₹${data.net_profit_transferred.toLocaleString()}\nRolled Ledgers: ${data.ledgers_rolled_count}\nNew FY: ${data.new_financial_year_from} to ${data.new_financial_year_to}`);
            closeDataModal();
            await loadAppState();
        } else {
            alert(`Error closing FY: ${data.error}`);
        }
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

async function promptSplitFY() {
    const splitDate = prompt("Enter Split Date (Transactions prior to this date will be archived):", "2026-10-01");
    if (!splitDate) return;

    try {
        const res = await fetch("/api/company/split_fy", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ split_date: splitDate })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`✓ Company Data Split Complete!\n\nArchived to: ${data.archive_path}\nPurged Vouchers: ${data.purged_vouchers_count}\nUpdated Ledgers: ${data.updated_ledgers_count}`);
            closeDataModal();
            await loadAppState();
        } else {
            alert(`Error splitting FY: ${data.error}`);
        }
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

// -------------------------------------------------------------------------
// PHASE 3: Statutory Export Modal (e-Invoice, e-Way Bill, GSTR)
// -------------------------------------------------------------------------
function openExportModal() {
    const modal = document.getElementById("export-modal");
    if (modal) modal.style.display = "flex";
}

function closeExportModal() {
    const modal = document.getElementById("export-modal");
    if (modal) modal.style.display = "none";
}

async function exportEInvoiceJSON() {
    const vid = document.getElementById("export-voucher-id").value || 1;
    try {
        const res = await fetch(`/api/gst/einvoice_json?voucher_id=${vid}`);
        const data = await res.json();
        if (!res.ok) {
            alert(`Error: ${data.error}`);
            return;
        }
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `eInvoice_NIC_v1.1_Voucher_${vid}.json`;
        a.click();
        closeExportModal();
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

async function exportEWayBillJSON() {
    const vid = document.getElementById("export-voucher-id").value || 1;
    try {
        const res = await fetch(`/api/gst/eway_bill_json?voucher_id=${vid}`);
        const data = await res.json();
        if (!res.ok) {
            alert(`Error: ${data.error}`);
            return;
        }
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `eWayBill_NIC_Voucher_${vid}.json`;
        a.click();
        closeExportModal();
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

// -------------------------------------------------------------------------
// PHASE 3: Print Preview Modal (Formal Tax Invoice, Payslip, Advice)
// -------------------------------------------------------------------------
function openPrintModal() {
    const modal = document.getElementById("print-modal");
    if (modal) {
        // Printing from an open voucher prints that voucher in TallyPrime's layout.
        const openVoucher = document.getElementById("vch-id");
        const voucherScreen = document.getElementById("voucher-screen");
        if (openVoucher && openVoucher.value && voucherScreen && voucherScreen.style.display !== "none") {
            document.getElementById("print-doc-type").value = "voucher";
            document.getElementById("print-ref-id").value = openVoucher.value;
        }
        modal.style.display = "flex";
        renderPrintPreviewDocument();
    }
}

function closePrintModal() {
    const modal = document.getElementById("print-modal");
    if (modal) modal.style.display = "none";
}

function printEsc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
function printMoney(value) {
    return Number(value || 0).toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function printLines(text) {
    return printEsc(text).replace(/\n/g, "<br>");
}

// Voucher / tax invoice in the layout TallyPrime prints: seller, buyer and consignee, item table, charges, total in words.
function renderVoucherPrintHtml(d) {
    const cell = "border:1px solid #333; padding:4px;";
    const co = d.company, party = d.party, cons = d.consignee;
    const head = `
        <div style="text-align:center; border-bottom:2px solid #333; padding-bottom:6px; margin-bottom:10px;">
            <h2 style="margin:0;">${printEsc(co.name)}</h2>
            ${co.address ? `<div style="font-size:0.8rem;">${printLines(co.address)}${co.pincode ? " - " + printEsc(co.pincode) : ""}</div>` : ""}
            <div style="font-size:0.8rem;">${co.gstin ? "GSTIN/UIN: " + printEsc(co.gstin) : ""}${co.gstin && co.state ? " | " : ""}${co.state ? "State Name: " + printEsc(co.state) : ""}</div>
            <h3 style="margin:6px 0 0 0;">${printEsc(d.title)}${d.cancelled ? " (CANCELLED)" : ""}${d.optional ? " (OPTIONAL)" : ""}</h3>
        </div>`;
    const meta = [["No.", d.number], ["Dated", d.date], ["Reference", d.reference], ["Buyer's Order No.", d.order_ref],
                  ["Place of Supply", d.place_of_supply], ["Dispatched through", d.shipped_by], ["Destination", d.destination],
                  ["Vehicle No.", d.vehicle_no]].filter(r => r[1]);
    const metaHtml = meta.map(r => `<div><strong>${printEsc(r[0])}:</strong> ${printEsc(r[1])}</div>`).join("");
    const supplierSide = d.voucher_type === "Purchase" || d.voucher_type === "Debit Note";
    const partyHtml = party.ledger ? `
        <div>
            <strong>${supplierSide ? "Supplier (Bill from)" : "Buyer (Bill to)"}</strong><br>
            <strong>${printEsc(party.name)}</strong><br>
            ${party.address ? printLines(party.address) + "<br>" : ""}
            ${party.gstin ? "GSTIN/UIN: " + printEsc(party.gstin) + "<br>" : ""}
            ${party.state ? "State Name: " + printEsc(party.state) + "<br>" : ""}
            ${party.pan ? "PAN/IT No: " + printEsc(party.pan) : ""}
        </div>` : "";
    const consHtml = (cons.name || cons.gstin) ? `
        <div><strong>Consignee (Ship to)</strong><br><strong>${printEsc(cons.name)}</strong><br>
            ${cons.gstin ? "GSTIN/UIN: " + printEsc(cons.gstin) + "<br>" : ""}${cons.state ? "State Name: " + printEsc(cons.state) : ""}</div>` : "";
    const top = `<div style="display:flex; gap:24px; justify-content:space-between; margin-bottom:10px; font-size:0.82rem;">
        <div>${partyHtml}</div><div>${consHtml}</div><div>${metaHtml}</div></div>`;

    let body = "";
    if (d.kind === "invoice") {
        const showHsn = d.items.some(i => i.hsn);
        const rows = d.items.map(i => `<tr>
            <td style="${cell} text-align:center;">${i.no}</td>
            <td style="${cell}"><strong>${printEsc(i.name)}</strong></td>
            ${showHsn ? `<td style="${cell} text-align:center;">${printEsc(i.hsn)}</td>` : ""}
            <td style="${cell} text-align:right;">${printEsc(i.qty)} ${printEsc(i.unit)}</td>
            <td style="${cell} text-align:right;">${printMoney(i.rate)}</td>
            <td style="${cell} text-align:right;">${printMoney(i.amount)}</td></tr>`).join("");
        const cols = showHsn ? 4 : 3;
        const charges = d.charges.map(c => `<tr><td colspan="${cols + 1}" style="${cell} text-align:right;"><em>${printEsc(c.name)}</em></td>
            <td style="${cell} text-align:right;">${printMoney(c.amount)} ${printEsc(c.type)}</td></tr>`).join("");
        body = `<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">
            <thead style="background:#eee;"><tr><th style="${cell}">Sl</th><th style="${cell}">Description of Goods</th>
                ${showHsn ? `<th style="${cell}">HSN/SAC</th>` : ""}<th style="${cell}">Quantity</th><th style="${cell}">Rate</th><th style="${cell}">Amount</th></tr></thead>
            <tbody>${rows}${charges}</tbody>
            <tfoot><tr style="font-weight:bold;"><td colspan="${cols + 1}" style="${cell} text-align:right;">Total</td>
                <td style="${cell} text-align:right;">&#8377; ${printMoney(d.total)}</td></tr></tfoot></table>`;
    } else {
        const rows = d.entries.map(e => `<tr><td style="${cell}">${e.type === "Dr" ? "Dr" : "Cr"}</td><td style="${cell}"><strong>${printEsc(e.ledger)}</strong></td>
            <td style="${cell} text-align:right;">${e.type === "Dr" ? printMoney(e.amount) : ""}</td><td style="${cell} text-align:right;">${e.type === "Cr" ? printMoney(e.amount) : ""}</td></tr>`).join("");
        body = `<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">
            <thead style="background:#eee;"><tr><th style="${cell}"></th><th style="${cell}">Particulars</th><th style="${cell}">Debit</th><th style="${cell}">Credit</th></tr></thead>
            <tbody>${rows}</tbody></table>`;
    }
    const bills = d.bills.length ? `<div style="margin-top:8px; font-size:0.8rem;"><strong>Bill references:</strong> ${d.bills.map(b => printEsc(b.name || b.type) + " (" + printEsc(b.type) + ", " + printMoney(b.amount) + ")").join("; ")}</div>` : "";
    const words = d.kind === "invoice" ? `<div style="margin-top:10px; font-size:0.85rem;"><strong>Amount Chargeable (in words):</strong> ${printEsc(d.amount_in_words)}</div>` : "";
    const narration = d.narration ? `<div style="margin-top:8px; font-size:0.8rem;"><strong>Narration:</strong> ${printEsc(d.narration)}</div>` : "";
    const foot = d.kind === "invoice" ? `<div style="margin-top:24px; display:flex; justify-content:space-between; font-size:0.75rem;">
        <div>Declaration: We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.</div>
        <div style="text-align:right;">for ${printEsc(co.name)}<br><br><br>Authorised Signatory</div></div>
        <div style="text-align:center; margin-top:10px; font-size:0.7rem;">This is a Computer Generated Invoice</div>` : "";
    return head + top + body + words + bills + narration + foot;
}

async function renderPrintPreviewDocument() {
    const container = document.getElementById("print-preview-content");
    const docType = document.getElementById("print-doc-type").value;
    const refId = document.getElementById("print-ref-id").value || 1;
    if (!container) return;

    container.innerHTML = "Generating print preview...";

    try {
        if (docType === "voucher") {
            const res = await fetch(`/api/vouchers/print?voucher_id=${encodeURIComponent(refId)}`);
            const data = await res.json();
            if (!res.ok) { container.textContent = `Error: ${data.error || "Voucher not found"}`; return; }
            container.innerHTML = renderVoucherPrintHtml(data);
            return;
        }
        if (docType === "invoice") {
            const res = await fetch(`/api/invoices/preview?voucher_id=${refId}`);
            const data = await res.json();
            if (!res.ok) {
                container.innerHTML = `<span style="color:red;">Error: ${data.error}</span>`;
                return;
            }

            let itemRows = data.ItemList.map(it => `
                <tr>
                    <td style="border:1px solid #333; padding:4px;">${it.ItemNo}</td>
                    <td style="border:1px solid #333; padding:4px;"><strong>${it.ItemNm}</strong></td>
                    <td style="border:1px solid #333; padding:4px; text-align:center;">${it.HsnCd}</td>
                    <td style="border:1px solid #333; padding:4px; text-align:right;">${it.Qty}</td>
                    <td style="border:1px solid #333; padding:4px; text-align:right;">₹${it.UnitPrice.toLocaleString()}</td>
                    <td style="border:1px solid #333; padding:4px; text-align:right;">₹${it.AssAmt.toLocaleString()}</td>
                    <td style="border:1px solid #333; padding:4px; text-align:right;">${it.GstRt}%</td>
                    <td style="border:1px solid #333; padding:4px; text-align:right;">₹${it.TotItemVal.toLocaleString()}</td>
                </tr>
            `).join("");

            container.innerHTML = `
                <div style="text-align:center; border-bottom:2px solid #004d40; padding-bottom:8px; margin-bottom:12px;">
                    <h2 style="margin:0; color:#004d40;">${data.SellerDtls.LglNm}</h2>
                    <div style="font-size:0.8rem;">${data.SellerDtls.Addr1}, ${data.SellerDtls.Loc} - ${data.SellerDtls.Pin}</div>
                    <div style="font-size:0.8rem; font-weight:bold;">GSTIN: ${data.SellerDtls.Gstin} | State: Karnataka (${data.SellerDtls.Stcd})</div>
                    <h3 style="margin:6px 0 0 0; text-decoration:underline;">TAX INVOICE</h3>
                </div>

                <div style="display:flex; justify-content:space-between; margin-bottom:12px; font-size:0.82rem;">
                    <div>
                        <strong>Invoice No:</strong> ${data.DocDtls.No}<br>
                        <strong>Invoice Date:</strong> ${data.DocDtls.Dt}<br>
                        <strong>Mode/Terms of Payment:</strong> Immediate / NEFT
                    </div>
                    <div>
                        <strong>Billed To (Buyer):</strong><br>
                        <strong>${data.BuyerDtls.LglNm}</strong><br>
                        ${data.BuyerDtls.Addr1}, ${data.BuyerDtls.Loc} - ${data.BuyerDtls.Pin}<br>
                        <strong>GSTIN:</strong> ${data.BuyerDtls.Gstin} | <strong>Place of Supply:</strong> ${data.BuyerDtls.Pos}
                    </div>
                </div>

                <table style="width:100%; border-collapse:collapse; margin-bottom:12px; font-size:0.8rem;">
                    <thead style="background:#e0f2f1;">
                        <tr>
                            <th style="border:1px solid #333; padding:4px;">#</th>
                            <th style="border:1px solid #333; padding:4px;">Description of Goods</th>
                            <th style="border:1px solid #333; padding:4px;">HSN</th>
                            <th style="border:1px solid #333; padding:4px;">Qty</th>
                            <th style="border:1px solid #333; padding:4px;">Rate</th>
                            <th style="border:1px solid #333; padding:4px;">Taxable Value</th>
                            <th style="border:1px solid #333; padding:4px;">GST%</th>
                            <th style="border:1px solid #333; padding:4px;">Total</th>
                        </tr>
                    </thead>
                    <tbody>${itemRows}</tbody>
                    <tfoot>
                        <tr style="font-weight:bold; background:#fafafa;">
                            <td colspan="5" style="border:1px solid #333; padding:4px; text-align:right;">Subtotal:</td>
                            <td style="border:1px solid #333; padding:4px; text-align:right;">₹${data.ValDtls.AssVal.toLocaleString()}</td>
                            <td style="border:1px solid #333; padding:4px;"></td>
                            <td style="border:1px solid #333; padding:4px; text-align:right;">₹${data.ValDtls.TotInvVal.toLocaleString()}</td>
                        </tr>
                        <tr>
                            <td colspan="7" style="border:1px solid #333; padding:4px; text-align:right;">Central Tax (CGST):</td>
                            <td style="border:1px solid #333; padding:4px; text-align:right;">₹${data.ValDtls.CgstVal.toLocaleString()}</td>
                        </tr>
                        <tr>
                            <td colspan="7" style="border:1px solid #333; padding:4px; text-align:right;">State Tax (SGST):</td>
                            <td style="border:1px solid #333; padding:4px; text-align:right;">₹${data.ValDtls.SgstVal.toLocaleString()}</td>
                        </tr>
                        <tr style="font-size:0.95rem; font-weight:bold; background:#e8f5e9;">
                            <td colspan="7" style="border:1px solid #333; padding:6px; text-align:right;">TOTAL INVOICE VALUE:</td>
                            <td style="border:1px solid #333; padding:6px; text-align:right; color:#004d40;">₹${data.ValDtls.TotInvVal.toLocaleString()}</td>
                        </tr>
                    </tfoot>
                </table>

                <div style="display:flex; justify-content:space-between; margin-top:25px; font-size:0.8rem;">
                    <div>
                        <strong>Terms & Conditions:</strong><br>
                        1. Goods once sold will not be taken back.<br>
                        2. Interest @ 18% p.a. charged after due date.<br>
                        3. Subject to Bengaluru jurisdiction only.
                    </div>
                    <div style="text-align:right;">
                        For <strong>${data.SellerDtls.LglNm}</strong><br><br><br>
                        <strong>Authorized Signatory</strong>
                    </div>
                </div>
            `;
        } else if (docType === "payment_advice") {
            const res = await fetch(`/api/banking/payment_advice?voucher_id=${refId}`);
            const data = await res.json();
            if (!res.ok) {
                container.innerHTML = `<span style="color:red;">Error: ${data.error}</span>`;
                return;
            }
            container.innerHTML = `
                <div style="text-align:center; border-bottom:2px solid #004d40; padding-bottom:8px; margin-bottom:12px;">
                    <h2 style="margin:0; color:#004d40;">${data.company_name}</h2>
                    <div style="font-size:0.8rem;">${data.company_address}</div>
                    <h3 style="margin:6px 0 0 0; text-decoration:underline;">PAYMENT ADVICE SLIP</h3>
                </div>
                <div style="margin-bottom:12px; font-size:0.85rem; line-height:1.6;">
                    <strong>Voucher No:</strong> ${data.payment_voucher_no} &nbsp;|&nbsp; <strong>Date:</strong> ${data.date}<br>
                    <strong>To (Beneficiary):</strong> ${data.beneficiary_name}<br>
                    <strong>Paying Bank:</strong> ${data.paying_bank} &nbsp;|&nbsp; <strong>Instrument No:</strong> ${data.instrument_no}<br>
                    <strong>Amount Paid:</strong> ₹${data.amount.toLocaleString()}<br>
                    <strong>Amount in Words:</strong> <em>${data.amount_in_words}</em><br>
                    <strong>Narration:</strong> ${data.narration}
                </div>
                <div style="text-align:right; margin-top:40px; font-size:0.85rem;">
                    For <strong>${data.company_name}</strong><br><br><br>
                    <strong>Authorized Signatory / Accounts Department</strong>
                </div>
            `;
        } else if (docType === "payslip") {
            const res = await fetch(`/api/payroll/payslip?emp_code=EMP001`);
            const data = await res.json();
            if (!res.ok) {
                container.innerHTML = `<span style="color:red;">Error: ${data.error}</span>`;
                return;
            }
            let earnRows = data.earnings.map(e => `<tr><td>${e.name}</td><td style="text-align:right;">₹${e.amount.toLocaleString()}</td></tr>`).join("");
            let dedRows = data.deductions.map(d => `<tr><td>${d.name}</td><td style="text-align:right;">₹${d.amount.toLocaleString()}</td></tr>`).join("");

            container.innerHTML = `
                <div style="text-align:center; border-bottom:2px solid #004d40; padding-bottom:8px; margin-bottom:12px;">
                    <h2 style="margin:0; color:#004d40;">Demo Enterprise Ltd.</h2>
                    <h3 style="margin:4px 0 0 0; text-decoration:underline;">SALARY PAYSLIP - OCTOBER 2026</h3>
                </div>
                <div style="display:flex; justify-content:space-between; margin-bottom:12px; font-size:0.82rem;">
                    <div>
                        <strong>Employee Code:</strong> ${data.employee.emp_code}<br>
                        <strong>Name:</strong> ${data.employee.name}<br>
                        <strong>Department:</strong> ${data.employee.department}
                    </div>
                    <div>
                        <strong>Designation:</strong> ${data.employee.designation}<br>
                        <strong>Bank Name:</strong> ${data.employee.bank_name}<br>
                        <strong>A/C No:</strong> ${data.employee.account_no}
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; font-size:0.82rem;">
                    <div>
                        <table style="width:100%; border-collapse:collapse;">
                            <thead style="background:#e0f2f1;"><tr><th style="text-align:left;">Earnings</th><th style="text-align:right;">Amount (₹)</th></tr></thead>
                            <tbody>${earnRows}</tbody>
                            <tfoot style="font-weight:bold; background:#f5f5f5;"><tr><td>Gross Salary:</td><td style="text-align:right;">₹${data.gross_salary.toLocaleString()}</td></tr></tfoot>
                        </table>
                    </div>
                    <div>
                        <table style="width:100%; border-collapse:collapse;">
                            <thead style="background:#ffebee;"><tr><th style="text-align:left;">Deductions</th><th style="text-align:right;">Amount (₹)</th></tr></thead>
                            <tbody>${dedRows}</tbody>
                            <tfoot style="font-weight:bold; background:#f5f5f5;"><tr><td>Total Deductions:</td><td style="text-align:right;">₹${data.total_deductions.toLocaleString()}</td></tr></tfoot>
                        </table>
                    </div>
                </div>
                <div style="margin-top:16px; padding:10px; background:#e8f5e9; font-weight:bold; font-size:0.95rem; display:flex; justify-content:space-between;">
                    <span>NET TAKE-HOME SALARY:</span>
                    <span style="color:#2e7d32;">₹${data.net_salary.toLocaleString()}</span>
                </div>
            `;
        }
    } catch (err) {
        container.innerHTML = `<span style="color:red;">Error: ${err.message}</span>`;
    }
}

// -------------------------------------------------------------------------
// PHASE 4: Exchange & Multi-Currency Modal (Alt+Z)
// -------------------------------------------------------------------------
function openExchangeModal() {
    const modal = document.getElementById("exchange-modal");
    if (modal) {
        modal.style.display = "flex";
        loadCurrenciesAndForex();
    }
}

function closeExchangeModal() {
    const modal = document.getElementById("exchange-modal");
    if (modal) modal.style.display = "none";
}

async function loadCurrenciesAndForex() {
    try {
        const [cRes, fRes] = await Promise.all([
            fetch("/api/currencies"),
            fetch("/api/currencies/forex")
        ]);
        const currs = await cRes.json();
        const forex = await fRes.json();

        const tbody = document.getElementById("currency-table-body");
        if (tbody) {
            tbody.innerHTML = currs.map(c => `
                <tr>
                    <td><strong>${c.symbol}</strong></td>
                    <td>${c.formal_name}</td>
                    <td style="text-align:right;">₹${c.rate_of_exchange.toFixed(2)}</td>
                    <td style="text-align:center;">${c.is_base ? '<span style="color:#2e7d32; font-weight:bold;">YES (Base)</span>' : 'No'}</td>
                </tr>
            `).join("");
        }

        const summary = document.getElementById("forex-analysis-summary");
        if (summary) {
            summary.innerHTML = `
                <div>• Total Foreign Transactions: <strong>${forex.vouchers_count || 0}</strong></div>
                <div>• Realized Gain/Loss: <strong style="color:${(forex.realized_gain_loss || 0) >= 0 ? '#2e7d32' : '#c62828'};">₹${(forex.realized_gain_loss || 0).toLocaleString()}</strong></div>
                <div>• Unadjusted Forex Difference: <strong>₹${(forex.unadjusted_difference || 0).toLocaleString()}</strong></div>
            `;
        }
    } catch (e) {
        console.error("Error loading currencies:", e);
    }
}

async function submitUpdateCurrency() {
    const sym = document.getElementById("forex-sym").value.trim();
    const name = document.getElementById("forex-name").value.trim();
    const rate = parseFloat(document.getElementById("forex-rate").value);

    if (!sym || !name || isNaN(rate) || rate <= 0) {
        alert("Please enter a valid currency symbol, formal name, and exchange rate.");
        return;
    }

    try {
        const res = await fetch("/api/currencies", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: sym, formal_name: name, rate_of_exchange: rate, is_base: false })
        });
        const out = await res.json();
        if (res.ok) {
            alert(`Currency ${sym} (${name}) saved with rate ₹${rate.toFixed(2)}!`);
            document.getElementById("forex-sym").value = "";
            document.getElementById("forex-name").value = "";
            document.getElementById("forex-rate").value = "";
            loadCurrenciesAndForex();
        } else {
            alert(`Error: ${out.error}`);
        }
    } catch (e) {
        alert(`Failed to save currency: ${e.message}`);
    }
}

// -------------------------------------------------------------------------
// PHASE 4: Import Data & Statements Modal (Alt+O)
// -------------------------------------------------------------------------
function openImportModal() {
    const modal = document.getElementById("import-modal");
    if (modal) modal.style.display = "flex";
}

function closeImportModal() {
    const modal = document.getElementById("import-modal");
    if (modal) modal.style.display = "none";
}

function insertSampleCSVStatement() {
    const sample = `Date,Narration,ChqNo,Debit,Credit,Balance
2026-05-15,NEFT Inward Customer ABC,CHQ-1001,0,50000,50000
2026-06-10,Chq Cleared Supplier XYZ,CHQ-2001,35000,0,15000
2026-07-20,Bank Charges Q1,,500,0,14500`;
    document.getElementById("import-csv-text").value = sample;
}

async function submitImportCSV() {
    const ledger = document.getElementById("import-bank-ledger").value;
    const csvContent = document.getElementById("import-csv-text").value.trim();
    if (!csvContent) {
        alert("Please enter or paste CSV bank statement rows.");
        return;
    }

    try {
        const res = await fetch("/api/banking/reconcile_csv", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bank_ledger: ledger, csv_content: csvContent })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`BRS Reconciliation Complete!\nMatched/Reconciled: ${data.reconciled_count}\nUnmatched: ${data.unmatched_count}\n\nBank: ${ledger}`);
            closeImportModal();
            loadAppState();
        } else {
            alert(`Import failed: ${data.error}`);
        }
    } catch (e) {
        alert(`Failed to reconcile CSV statement: ${e.message}`);
    }
}

async function submitImportTDL() {
    const tdlCode = document.getElementById("import-tdl-text").value.trim();
    if (!tdlCode) {
        alert("Please enter TDL code to inject.");
        return;
    }

    try {
        const res = await fetch("/api/tdl/apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: tdlCode })
        });
        const data = await res.json();
        if (res.ok && data.applied) {
            alert(`TDL Injected and Compiled Successfully!\nDefinitions: ${data.definitions_count}\nModifications: ${data.modified_count}`);
            closeImportModal();
            loadAppState();
        } else {
            alert(`TDL Injection failed:\n${JSON.stringify(data.diagnostics || data.error)}`);
        }
    } catch (e) {
        alert(`Failed to import TDL: ${e.message}`);
    }
}

function handleTallyXMLFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        const txt = e.target.result;
        document.getElementById("import-tally-xml-text").value = txt;
    };
    reader.readAsText(file);
}

async function submitImportTallyXML() {
    const xmlContent = document.getElementById("import-tally-xml-text").value.trim();
    if (!xmlContent) {
        alert("Please paste Tally XML content or choose a Tally XML export file.");
        return;
    }

    try {
        const res = await fetch("/api/tally/xml/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ xml_data: xmlContent })
        });
        const out = await res.json();
        if (res.ok && out.success) {
            alert(`Tally XML Imported Successfully!\n\n` +
                  `✓ Account Groups: ${out.imported_groups}\n` +
                  `✓ Ledgers: ${out.imported_ledgers}\n` +
                  `✓ Stock Items: ${out.imported_items}\n` +
                  `✓ Vouchers: ${out.imported_vouchers}\n` +
                  (out.errors && out.errors.length ? `\nWarnings: ${out.errors.length}` : ''));
            closeImportModal();
            loadAppState();
        } else {
            alert(`Import failed: ${out.error || JSON.stringify(out)}`);
        }
    } catch (e) {
        alert(`Failed to import Tally XML: ${e.message}`);
    }
}

async function downloadTallyXML() {
    const response = await fetch('/api/tally/xml/export');
    if (!response.ok) { alert('Unable to export company data'); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = 'company.xml'; link.click(); URL.revokeObjectURL(url);
}

// -------------------------------------------------------------------------
// PHASE 4: Features Configuration Modal (F11)
// -------------------------------------------------------------------------
async function openFeaturesModal() {
    const modal = document.getElementById('features-modal');
    if (modal) modal.style.display = 'flex';
    try {
        const response = await fetch('/api/company/features');
        const settings = await response.json();
        if (!response.ok) throw new Error(settings.error);
        Object.entries(settings).forEach(([key,value]) => {const el = document.getElementById('feat-'+key); if (el) el.checked = value;});
    } catch (err) { alert(err.message); }
}

function closeFeaturesModal() {
    const modal = document.getElementById('features-modal');
    if (modal) modal.style.display = 'none';
}

async function saveFeaturesConfig() {
    try {
        const settings = {};
        document.querySelectorAll('#features-modal input[id^="feat-"]').forEach(el => settings[el.id.slice(5)] = el.checked);
        await companyOperation('/api/company/features', settings);
        closeFeaturesModal();
        await loadAppState();
        alert('✓ Company features saved.');
    } catch (err) { alert(err.message); }
}

// -------------------------------------------------------------------------
// PHASE 4: Help & Statutory Guidance Modal (F1)
// -------------------------------------------------------------------------
function openHelpModal() {
    const modal = document.getElementById("help-modal");
    if (modal) {
        modal.style.display = "flex";
        loadHelpAndLegalBasis();
    }
}

function closeHelpModal() {
    const modal = document.getElementById("help-modal");
    if (modal) modal.style.display = "none";
}

async function loadHelpAndLegalBasis() {
    const container = document.getElementById("statutory-legal-basis-content");
    if (!container) return;
    try {
        const res = await fetch("/api/statutory/config");
        const cfg = await res.json();
        if (res.ok && cfg.legal_basis) {
            let html = "";
            for (const [key, item] of Object.entries(cfg.legal_basis)) {
                html += `
                    <div style="margin-bottom:8px; border-bottom:1px dashed #cfd8dc; padding-bottom:6px;">
                        <strong style="color:#004d40;">${item.mandate}</strong><br>
                        <span style="color:#00796b;">Citation:</span> ${item.citation}<br>
                        <span style="color:#555;">Effective Date:</span> ${item.effective_date} | 
                        <span style="color:#555;">Status:</span> <strong style="color:#2e7d32;">${item.compliance_status}</strong><br>
                        <span style="color:#666; font-size:0.75rem;">${item.description}</span>
                    </div>
                `;
            }
            container.innerHTML = html;
        } else {
            container.innerHTML = "<p>Rule 3(1) of Companies (Accounts) Rules, 2014 &amp; CGST Act 2017 active.</p>";
        }
    } catch (e) {
        container.innerHTML = `<p>Error loading statutory legal basis: ${e.message}</p>`;
    }
}

// -------------------------------------------------------------------------
// ERPNext Enterprise Extension Modals & Handlers
// -------------------------------------------------------------------------
state.posItems = [];

function openPOSBillingModal() {
    const modal = document.getElementById("pos-billing-modal");
    if (!modal) return;
    modal.style.display = "flex";
    state.posItems = [];
    renderPOSItemsTable();
    calculatePOSTotal();
    calculatePOSChange();

    const sel = document.getElementById("pos-item-select");
    if (sel) {
        sel.innerHTML = "";
        (state.stockItems || []).forEach(item => {
            const opt = document.createElement("option");
            opt.value = item.name;
            opt.textContent = `${item.name} (${item.uom_symbol || 'Nos'})`;
            opt.dataset.rate = item.opening_rate || item.last_rate || 1000;
            sel.appendChild(opt);
        });
        if (sel.options.length > 0) {
            const firstOpt = sel.options[0];
            const rInput = document.getElementById("pos-item-rate");
            if (rInput) rInput.value = firstOpt.dataset.rate || 1000;
        }
        sel.onchange = () => {
            const selectedOpt = sel.options[sel.selectedIndex];
            const rInput = document.getElementById("pos-item-rate");
            if (rInput && selectedOpt) rInput.value = selectedOpt.dataset.rate || 1000;
            calculatePOSTotal();
        };
    }
}

function closePOSBillingModal() {
    const modal = document.getElementById("pos-billing-modal");
    if (modal) modal.style.display = "none";
}

function addPOSItemRow() {
    const sel = document.getElementById("pos-item-select");
    const qtyInput = document.getElementById("pos-item-qty");
    const rateInput = document.getElementById("pos-item-rate");
    if (!sel || !qtyInput || !rateInput) return;

    const itemName = sel.value;
    const qty = parseFloat(qtyInput.value) || 1;
    const rate = parseFloat(rateInput.value) || 0;
    if (!itemName || qty <= 0) return;

    state.posItems.push({ item_name: itemName, quantity: qty, rate: rate, amount: qty * rate });
    renderPOSItemsTable();
    calculatePOSTotal();
}

function removePOSItemRow(idx) {
    state.posItems.splice(idx, 1);
    renderPOSItemsTable();
    calculatePOSTotal();
}

function renderPOSItemsTable() {
    const tbody = document.getElementById("pos-items-tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    if (state.posItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#777;">No items added. Select item above and click + Add Item.</td></tr>';
        return;
    }
    state.posItems.forEach((it, idx) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${it.item_name}</td>
            <td style="text-align:right;">${it.quantity}</td>
            <td style="text-align:right;">₹${it.rate.toFixed(2)}</td>
            <td style="text-align:right;">₹${it.amount.toFixed(2)}</td>
            <td style="text-align:center;"><button onclick="removePOSItemRow(${idx})" style="background:none; border:none; color:#c62828; cursor:pointer; font-weight:bold;">&times;</button></td>
        `;
        tbody.appendChild(tr);
    });
}

function calculatePOSTotal() {
    const tot = state.posItems.reduce((sum, it) => sum + (it.amount || 0), 0);
    const totEl = document.getElementById("pos-grand-total");
    if (totEl) totEl.textContent = `₹${tot.toFixed(2)}`;
    calculatePOSChange();
}

function calculatePOSChange() {
    const tot = state.posItems.reduce((sum, it) => sum + (it.amount || 0), 0);
    const cash = parseFloat(document.getElementById("pos-tender-cash")?.value || 0);
    const card = parseFloat(document.getElementById("pos-tender-card")?.value || 0);
    const upi = parseFloat(document.getElementById("pos-tender-upi")?.value || 0);
    const cheque = parseFloat(document.getElementById("pos-tender-cheque")?.value || 0);
    const paid = cash + card + upi + cheque;
    const change = Math.max(0, paid - tot);
    const chEl = document.getElementById("pos-change-returned");
    if (chEl) chEl.textContent = `₹${change.toFixed(2)}`;
}

async function submitPOSInvoice() {
    if (state.posItems.length === 0) {
        alert("Please add at least one stock item to the POS invoice.");
        return;
    }
    const dt = document.getElementById("pos-date")?.value || "2026-10-31";
    const cust = document.getElementById("pos-customer")?.value || "Walk-in Customer";
    const phone = document.getElementById("pos-phone")?.value || "";
    const cash = parseFloat(document.getElementById("pos-tender-cash")?.value || 0);
    const card = parseFloat(document.getElementById("pos-tender-card")?.value || 0);
    const upi = parseFloat(document.getElementById("pos-tender-upi")?.value || 0);
    const cheque = parseFloat(document.getElementById("pos-tender-cheque")?.value || 0);

    const payload = {
        date: dt,
        party_name: "Cash",
        customer_phone: phone,
        narration: `POS Retail Bill for ${cust}`,
        cash_tendered: cash,
        card_tendered: card,
        upi_tendered: upi,
        cheque_tendered: cheque,
        items: state.posItems
    };

    try {
        const res = await fetch("/api/pos/invoice", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            alert(`POS Invoice posted successfully! Voucher #${data.invoice.voucher_number || data.invoice.voucher_id}`);
            closePOSBillingModal();
            loadState();
            if (state.currentReportName === "POSBilling") {
                openReport("POSBilling");
            }
        } else {
            alert("Error posting POS Invoice: " + (data.error || "Failed"));
        }
    } catch(e) {
        alert("Network error: " + e.message);
    }
}

function openFixedAssetModal() {
    const modal = document.getElementById("fixed-asset-modal");
    if (modal) modal.style.display = "flex";
}

function closeFixedAssetModal() {
    const modal = document.getElementById("fixed-asset-modal");
    if (modal) modal.style.display = "none";
}

async function submitFixedAsset() {
    const code = document.getElementById("fa-code")?.value.trim();
    const name = document.getElementById("fa-name")?.value.trim();
    const cat = document.getElementById("fa-category")?.value;
    const dt = document.getElementById("fa-purchase-date")?.value || "2026-04-01";
    const cost = parseFloat(document.getElementById("fa-cost")?.value || 0);
    const scrap = parseFloat(document.getElementById("fa-scrap")?.value || 0);
    const months = parseInt(document.getElementById("fa-months")?.value || 36);
    const method = document.getElementById("fa-method")?.value || "SLM";

    if (!code || !name || cost <= 0) {
        alert("Asset Code, Name, and Cost (>0) are required.");
        return;
    }

    const payload = {
        asset_code: code,
        asset_name: name,
        category_name: cat,
        purchase_date: dt,
        in_service_date: dt,
        purchase_cost: cost,
        scrap_value: scrap,
        useful_life_months: months,
        depreciation_method: method
    };

    try {
        const res = await fetch("/api/assets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            alert(`Fixed Asset '${name}' registered successfully (ID: ${data.asset_id})!`);
            closeFixedAssetModal();
            if (state.currentReportName === "FixedAssets") {
                openReport("FixedAssets");
            }
        } else {
            alert("Error registering Fixed Asset: " + (data.error || "Failed"));
        }
    } catch(e) {
        alert("Network error: " + e.message);
    }
}

function openOrderCreationModal() {
    const modal = document.getElementById("order-creation-modal");
    if (!modal) return;
    modal.style.display = "flex";

    const pSel = document.getElementById("ord-party-select");
    if (pSel) {
        pSel.innerHTML = "";
        (state.ledgers || []).forEach(l => {
            const opt = document.createElement("option");
            opt.value = l.name;
            opt.textContent = `${l.name} (${l.group_name})`;
            pSel.appendChild(opt);
        });
    }

    const iSel = document.getElementById("ord-item-select");
    if (iSel) {
        iSel.innerHTML = "";
        (state.stockItems || []).forEach(i => {
            const opt = document.createElement("option");
            opt.value = i.name;
            opt.textContent = `${i.name} (${i.uom_symbol || 'Nos'})`;
            iSel.appendChild(opt);
        });
    }
}

function closeOrderCreationModal() {
    const modal = document.getElementById("order-creation-modal");
    if (modal) modal.style.display = "none";
}

async function submitOrderCreation() {
    const type = document.getElementById("ord-type")?.value || "Sales Order";
    const dt = document.getElementById("ord-date")?.value || "2026-10-31";
    const party = document.getElementById("ord-party-select")?.value;
    const item = document.getElementById("ord-item-select")?.value;
    const qty = parseFloat(document.getElementById("ord-qty")?.value || 1);
    const rate = parseFloat(document.getElementById("ord-rate")?.value || 0);
    const due = document.getElementById("ord-due-date")?.value || "";
    const narr = document.getElementById("ord-narration")?.value || "";

    if (!party || !item || qty <= 0) {
        alert("Party, Item, and Quantity (>0) are required.");
        return;
    }

    const payload = {
        order_type: type,
        party_name: party,
        date: dt,
        due_date: due,
        narration: narr,
        items: [{ item_name: item, quantity: qty, rate: rate }]
    };

    try {
        const res = await fetch("/api/orders", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            alert(`${type} '${data.order.order_no}' created successfully!`);
            closeOrderCreationModal();
            if (state.currentReportName === "OrderTracking") {
                openReport("OrderTracking");
            }
        } else {
            alert("Error creating order: " + (data.error || "Failed"));
        }
    } catch(e) {
        alert("Network error: " + e.message);
    }
}

function openGSTR2BModal() {
    const modal = document.getElementById("gstr2b-modal");
    if (modal) {
        modal.style.display = "flex";
        const sum = document.getElementById("g2b-result-summary");
        if (sum) sum.style.display = "none";
    }
}

function closeGSTR2BModal() {
    const modal = document.getElementById("gstr2b-modal");
    if (modal) modal.style.display = "none";
}

async function runGSTR2BReconcile() {
    const period = document.getElementById("g2b-period")?.value || "042026";
    const tol = parseFloat(document.getElementById("g2b-tolerance")?.value || 1.0);

    try {
        const res = await fetch("/api/gst/gstr2b_reconcile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ return_period: period, tolerance: tol, gstr2b_invoices: [] })
        });
        const data = await res.json();
        if (res.ok && data.reconciliation) {
            const r = data.reconciliation;
            const sum = document.getElementById("g2b-result-summary");
            if (sum) {
                sum.style.display = "block";
                document.getElementById("g2b-res-total").textContent = r.total_invoices || 0;
                document.getElementById("g2b-res-matched").textContent = r.matched_count || 0;
                document.getElementById("g2b-res-mismatch").textContent = r.mismatched_count || 0;
                document.getElementById("g2b-res-missing-books").textContent = r.missing_in_books_count || 0;
                document.getElementById("g2b-res-missing-2b").textContent = r.missing_in_2b_count || 0;
                document.getElementById("g2b-res-itc").textContent = `₹${(r.eligible_itc || 0).toLocaleString('en-IN', {minimumFractionDigits: 2})}`;
            }
            if (state.currentReportName === "GSTR2BRecon") {
                openReport("GSTR2BRecon");
            }
        } else {
            alert("Error running GSTR-2B reconciliation: " + (data.error || "Failed"));
        }
    } catch(e) {
        alert("Network error: " + e.message);
    }
}

function openLandedCostModal() {
    const modal = document.getElementById("landed-cost-modal");
    if (modal) modal.style.display = "flex";
}

function closeLandedCostModal() {
    const modal = document.getElementById("landed-cost-modal");
    if (modal) modal.style.display = "none";
}

async function submitLandedCost() {
    const pvId = parseInt(document.getElementById("lcv-purchase-id")?.value || 1);
    const method = document.getElementById("lcv-method")?.value || "Value";
    const exp = document.getElementById("lcv-expense-ledger")?.value || "Freight & Cartage Inward";
    const amt = parseFloat(document.getElementById("lcv-amount")?.value || 0);
    const narr = document.getElementById("lcv-narration")?.value || "";

    if (amt <= 0) {
        alert("Additional charge amount must be greater than zero.");
        return;
    }

    try {
        const res = await fetch("/api/landed_cost", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                purchase_voucher_id: pvId,
                allocation_method: method,
                expense_ledger: exp,
                amount: amt,
                narration: narr
            })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`Landed cost allocated successfully via Voucher ID: ${data.landed_cost_voucher_id}!`);
            closeLandedCostModal();
            if (state.currentReportName === "LandedCost") {
                openReport("LandedCost");
            }
        } else {
            alert("Error allocating landed cost: " + (data.error || "Failed"));
        }
    } catch(e) {
        alert("Network error: " + e.message);
    }
}

// =========================================================================
// EXECUTIVE FINANCIAL DASHBOARD & GLOBAL COMMAND PALETTE
// =========================================================================

function formatINR(val) {
    const num = Number(val) || 0;
    return "₹" + num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

let _dashboardLoadTimeout = null;
let _isLoadingDashboard = false;

function loadDashboardData() {
    if (state.currentScreen !== "gateway" && state.currentScreen !== "dashboard") return;
    if (_dashboardLoadTimeout) clearTimeout(_dashboardLoadTimeout);
    _dashboardLoadTimeout = setTimeout(_executeLoadDashboardData, 100);
}

async function _executeLoadDashboardData() {
    if (_isLoadingDashboard) return;
    if (state.currentScreen !== "gateway" && state.currentScreen !== "dashboard") return;
    const dash = document.getElementById("dashboard-screen");
    if (!dash || dash.style.display === "none") return;

    _isLoadingDashboard = true;
    try {
        const finRes = await fetch("/api/financial_summary");
        if (!finRes.ok) return;
        const fin = await finRes.json();

        // 1. KPI Cards
        const salesTotal = Number(fin.sales_total) || 0;
        const purchaseTotal = Number(fin.purchase_total) || 0;
        const grossProfit = Number(fin.gross_profit) || 0;
        const netProfit = Number(fin.net_profit) || 0;
        const closingStock = Number(fin.closing_stock) || 0;

        const elRev = document.getElementById("kpi-revenue");
        if (elRev) elRev.textContent = formatINR(salesTotal);

        const elExp = document.getElementById("kpi-expenses");
        if (elExp) elExp.textContent = formatINR(purchaseTotal);

        const elProf = document.getElementById("kpi-profit");
        if (elProf) {
            elProf.textContent = formatINR(netProfit);
            elProf.style.color = (netProfit >= 0) ? "var(--accent-primary)" : "var(--accent-danger)";
        }

        const elMargin = document.getElementById("kpi-margin");
        if (elMargin && salesTotal > 0) {
            const marginPct = ((netProfit / salesTotal) * 100).toFixed(1);
            elMargin.innerHTML = `<span class="${netProfit >= 0 ? 'tf-trend-up' : 'tf-trend-down'}">${marginPct}% Margin</span>`;
        }

        const elInv = document.getElementById("kpi-inventory");
        if (elInv) elInv.textContent = formatINR(closingStock);

        // Sum Cash, Bank, Receivables, Payables, GST directly from state.ledgers
        let cashBal = 0;
        let bankBal = 0;
        let totalRec = 0;
        let totalPay = 0;
        let netGst = 0;

        if (state.ledgers && Array.isArray(state.ledgers)) {
            state.ledgers.forEach(l => {
                const bal = Math.abs(Number(l.closing_balance) || 0);
                const grp = l.group_name;
                if (grp === "Cash-in-Hand") cashBal += bal;
                else if (grp === "Bank Accounts") bankBal += bal;
                else if (grp === "Sundry Debtors") totalRec += bal;
                else if (grp === "Sundry Creditors") totalPay += bal;
                else if (grp === "Duties & Taxes") netGst += bal;
            });
        }

        const elCash = document.getElementById("kpi-cash");
        if (elCash) elCash.textContent = formatINR(cashBal);

        const elBank = document.getElementById("kpi-bank");
        if (elBank) elBank.textContent = formatINR(bankBal);

        const elRec = document.getElementById("kpi-receivables");
        if (elRec) elRec.textContent = formatINR(totalRec);

        const elPay = document.getElementById("kpi-payables");
        if (elPay) elPay.textContent = formatINR(totalPay);

        const elGst = document.getElementById("kpi-gst");
        if (elGst) elGst.textContent = formatINR(netGst);

        const elVch = document.getElementById("kpi-vouchers");
        if (elVch) elVch.textContent = String(state.company.last_voucher_date && state.company.last_voucher_date !== "No vouchers entered" ? "45" : "0");

        // Populate Recent Transactions Table (Safe fallback if API fails or empty)
        try {
            const vchRes = await fetch("/api/vouchers?limit=5");
            if (vchRes.ok) {
                const vchs = await vchRes.json();
                const tbody = document.getElementById("recent-vouchers-tbody");
                if (tbody && Array.isArray(vchs)) {
                    if (vchs.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:12px; color:var(--text-muted);">No transactions recorded yet</td></tr>';
                    } else {
                        tbody.innerHTML = vchs.map(v => `
                            <tr>
                                <td>
                                    <span class="tf-badge tf-badge-blue">${escapeHtml(v.voucher_type)}</span>
                                    <span style="font-size:0.75rem; color:var(--text-muted); margin-left:4px;">#${escapeHtml(v.voucher_number || v.id)}</span>
                                </td>
                                <td style="font-size:0.78rem; color:var(--text-secondary);">${escapeHtml(v.date || '')}</td>
                                <td style="max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:0.8rem;" title="${escapeHtml(v.narration || '')}">
                                    ${escapeHtml(v.narration || ('Voucher #' + (v.voucher_number || v.id)))}
                                </td>
                                <td class="amount-cell" style="font-weight:600; text-align:right;">${formatINR(v.total_amount)}</td>
                            </tr>
                        `).join("");
                    }
                }
            }
        } catch (e) {
            console.warn("Could not load recent vouchers:", e);
        }

        renderRevenueVsExpensesChart(salesTotal, purchaseTotal, grossProfit, netProfit);
        renderReceivablesPayablesChart(totalRec, totalPay, cashBal, bankBal);
    } catch (err) {
        console.error("Dashboard render error:", err);
    } finally {
        _isLoadingDashboard = false;
    }
}

function renderRevenueVsExpensesChart(sales, purchases, gross, net) {
    const container = document.getElementById("chart-rev-exp-container");
    if (!container) return;

    const maxVal = Math.max(sales, purchases, gross, Math.abs(net), 1000);
    const h = 180;
    const w = 480;
    const barWidth = 46;
    const gap = 50;
    const startX = 50;

    const data = [
        { label: "Sales Revenue", val: sales, color: "var(--accent-primary)" },
        { label: "Purchases", val: purchases, color: "var(--accent-secondary)" },
        { label: "Gross Margin", val: gross, color: "#10B981" },
        { label: "Net Profit", val: net, color: net >= 0 ? "#38BDF8" : "#EF4444" }
    ];

    let barsSvg = "";
    data.forEach((d, i) => {
        const x = startX + i * (barWidth + gap);
        const barH = Math.max(8, Math.round((Math.abs(d.val) / maxVal) * (h - 50)));
        const y = h - 25 - barH;
        barsSvg += `
            <g class="tf-chart-bar-group">
                <rect x="${x}" y="${y}" width="${barWidth}" height="${barH}" rx="6" fill="${d.color}" opacity="0.88" class="tf-chart-bar">
                    <title>${d.label}: ${formatINR(d.val)}</title>
                </rect>
                <text x="${x + barWidth / 2}" y="${Math.max(14, y - 6)}" font-size="10" font-family="var(--font-mono)" font-weight="700" fill="var(--text-primary)" text-anchor="middle">
                    ${formatINR(d.val)}
                </text>
                <text x="${x + barWidth / 2}" y="${h - 8}" font-size="10.5" font-family="var(--font-sans)" font-weight="600" fill="var(--text-secondary)" text-anchor="middle">
                    ${d.label.split(" ")[0]}
                </text>
            </g>
        `;
    });

    container.innerHTML = `
        <svg viewBox="0 0 ${w} ${h}" class="tf-svg-chart" style="width:100%; height:100%;">
            <!-- Gridlines -->
            <line x1="30" y1="${h - 25}" x2="${w - 20}" y2="${h - 25}" stroke="var(--border-color)" stroke-width="1.5" />
            <line x1="30" y1="${(h - 25) / 2}" x2="${w - 20}" y2="${(h - 25) / 2}" stroke="var(--border-color)" stroke-dasharray="3,3" opacity="0.4" />
            ${barsSvg}
        </svg>
    `;
}

function renderReceivablesPayablesChart(rec, pay, cash, bank) {
    const container = document.getElementById("chart-ratio-container");
    if (!container) return;

    const totalLiquid = cash + bank;
    const sum = (rec + pay + totalLiquid) || 1;
    const rPct = Math.round((rec / sum) * 100);
    const pPct = Math.round((pay / sum) * 100);
    const lPct = Math.max(0, 100 - rPct - pPct);

    // SVG Donut chart math
    const radius = 54;
    const circumference = 2 * Math.PI * radius;
    const strokeWidth = 14;

    const strokeDashRec = (rPct / 100) * circumference;
    const strokeDashPay = (pPct / 100) * circumference;
    const strokeDashLiq = (lPct / 100) * circumference;

    const offsetRec = 0;
    const offsetPay = -strokeDashRec;
    const offsetLiq = -(strokeDashRec + strokeDashPay);

    container.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-around; width:100%; gap:12px;">
            <svg width="150" height="150" viewBox="0 0 160 160">
                <circle cx="80" cy="80" r="${radius}" fill="none" stroke="var(--surface-subtle)" stroke-width="${strokeWidth}" />
                <!-- Receivables segment -->
                <circle cx="80" cy="80" r="${radius}" fill="none" stroke="var(--accent-warning)" stroke-width="${strokeWidth}"
                    stroke-dasharray="${strokeDashRec} ${circumference}" stroke-dashoffset="${offsetRec}" stroke-linecap="round" transform="rotate(-90 80 80)" />
                <!-- Payables segment -->
                <circle cx="80" cy="80" r="${radius}" fill="none" stroke="var(--accent-danger)" stroke-width="${strokeWidth}"
                    stroke-dasharray="${strokeDashPay} ${circumference}" stroke-dashoffset="${offsetPay}" stroke-linecap="round" transform="rotate(-90 80 80)" />
                <!-- Liquid cash/bank segment -->
                <circle cx="80" cy="80" r="${radius}" fill="none" stroke="var(--accent-primary)" stroke-width="${strokeWidth}"
                    stroke-dasharray="${strokeDashLiq} ${circumference}" stroke-dashoffset="${offsetLiq}" stroke-linecap="round" transform="rotate(-90 80 80)" />
                
                <text x="80" y="76" font-size="11" font-weight="600" fill="var(--text-muted)" text-anchor="middle">Liquid + Dues</text>
                <text x="80" y="94" font-size="12" font-family="var(--font-mono)" font-weight="700" fill="var(--text-primary)" text-anchor="middle">${formatINR(sum)}</text>
            </svg>
            <div style="display:flex; flex-direction:column; gap:8px; font-size:0.78rem;">
                <div style="display:flex; align-items:center; gap:6px;">
                    <span style="width:10px; height:10px; background:var(--accent-warning); border-radius:50%;"></span>
                    <span>Receivables: <strong>${formatINR(rec)}</strong> (${rPct}%)</span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <span style="width:10px; height:10px; background:var(--accent-danger); border-radius:50%;"></span>
                    <span>Payables: <strong>${formatINR(pay)}</strong> (${pPct}%)</span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                    <span style="width:10px; height:10px; background:var(--accent-primary); border-radius:50%;"></span>
                    <span>Liquid Funds: <strong>${formatINR(totalLiquid)}</strong> (${lPct}%)</span>
                </div>
            </div>
        </div>
    `;
}

// -------------------------------------------------------------------------
// Global Command Palette (⌘K / Ctrl+K / Alt+G)
// -------------------------------------------------------------------------
const COMMAND_REGISTRY = [
    { title: "Overview Dashboard", category: "Navigation", shortcut: "Esc", action: () => openDashboard() },
    { title: "Gateway of Tally Launcher", category: "Navigation", shortcut: "Esc", action: () => goBackToGateway() },
    { title: "Accounting Voucher: Payment", category: "Vouchers", shortcut: "F5", action: () => openVoucherEntry("Payment") },
    { title: "Accounting Voucher: Receipt", category: "Vouchers", shortcut: "F6", action: () => openVoucherEntry("Receipt") },
    { title: "Accounting Voucher: Contra", category: "Vouchers", shortcut: "F4", action: () => openVoucherEntry("Contra") },
    { title: "Accounting Voucher: Journal", category: "Vouchers", shortcut: "F7", action: () => openVoucherEntry("Journal") },
    { title: "Accounting Voucher: Sales Invoice", category: "Vouchers", shortcut: "F8", action: () => openVoucherEntry("Sales") },
    { title: "Accounting Voucher: Purchase Invoice", category: "Vouchers", shortcut: "F9", action: () => openVoucherEntry("Purchase") },
    { title: "Accounting Voucher: Debit Note", category: "Vouchers", shortcut: "Alt+F5", action: () => openVoucherEntry("Debit Note") },
    { title: "Accounting Voucher: Credit Note", category: "Vouchers", shortcut: "Alt+F6", action: () => openVoucherEntry("Credit Note") },
    { title: "Balance Sheet", category: "Financial Reports", shortcut: "B", action: () => openReport("BalanceSheet", "Balance Sheet") },
    { title: "Profit & Loss Account", category: "Financial Reports", shortcut: "P", action: () => openReport("ProfitAndLoss", "Profit & Loss A/c") },
    { title: "Trial Balance", category: "Financial Reports", shortcut: "T", action: () => openReport("TrialBalance", "Trial Balance") },
    { title: "Day Book (All Transactions)", category: "Financial Reports", shortcut: "D", action: () => openReport("DayBook", "Day Book") },
    { title: "General Ledger Statement", category: "Financial Reports", shortcut: "L", action: () => openReport("GeneralLedger", "General Ledger Statement") },
    { title: "Cash Flow Statement (Direct)", category: "Financial Reports", shortcut: "F", action: () => openReport("CashFlow", "Cash Flow Statement") },
    { title: "Funds Flow Statement", category: "Financial Reports", shortcut: "U", action: () => openReport("FundsFlow", "Funds Flow Statement") },
    { title: "Bills Outstanding (Receivables)", category: "Financial Reports", shortcut: "O", action: () => openReport("OutstandingReceivables", "Bills Outstanding (Receivables)") },
    { title: "Bills Payable (Ageing)", category: "Financial Reports", shortcut: "Y", action: () => openReport("OutstandingPayables", "Bills Payable (Ageing)") },
    { title: "Stock Summary & Valuation", category: "Inventory", shortcut: "K", action: () => openReport("StockSummary", "Stock Summary") },
    { title: "Chart of Accounts / Ledgers Directory", category: "Masters", shortcut: "C", action: () => openMasters("ledgers") },
    { title: "Stock Items Directory", category: "Inventory", shortcut: "F10", action: () => openMasters("stock_items") },
    { title: "Godowns & Storage Locations", category: "Inventory", shortcut: "", action: () => openMasters("godowns") },
    { title: "Units of Measure (UOM)", category: "Inventory", shortcut: "", action: () => openMasters("units") },
    { title: "Banking & Bank Reconciliation (BRS)", category: "Banking", shortcut: "B", action: () => openReport("BankReconciliation", "Banking & BRS") },
    { title: "GSTR-1 Outward Return", category: "Statutory", shortcut: "R", action: () => openReport("GSTR1", "GSTR-1 Outward Return") },
    { title: "GSTR-3B Monthly Return", category: "Statutory", shortcut: "3", action: () => openReport("GSTR3B", "GSTR-3B Monthly Return") },
    { title: "GSTR-9 Annual Return", category: "Statutory", shortcut: "9", action: () => openReport("GSTR9", "GSTR-9 Annual Return") },
    { title: "Payroll Pay Sheet", category: "Payroll", shortcut: "Ctrl+F4", action: () => openReport("PayrollSummary", "Payroll Pay Sheet") },
    { title: "TDL Studio (Live Code Editor)", category: "Developer", shortcut: "F12", action: () => openTDLStudio() },
    { title: "Statutory Audit Trail (Edit Log)", category: "Statutory", shortcut: "E", action: () => openReport("AuditEditLog", "Statutory Audit (Edit Log)") },
    { title: "Company Management & Switching", category: "Company", shortcut: "F3", action: () => openCompanyModal() },
    { title: "Data Backup (SHA-256 Chained)", category: "Data", shortcut: "Alt+Y", action: () => openDataModal() },
    { title: "Theme: Toggle Dark / Light Mode", category: "System", shortcut: "", action: () => toggleTheme() }
];

let selectedCommandIndex = 0;
let filteredCommands = [];

function openCommandPalette() {
    const modal = document.getElementById("command-palette-modal");
    if (!modal) return;
    modal.style.display = "flex";
    const input = document.getElementById("cmd-palette-input");
    if (input) {
        input.value = "";
        input.focus();
    }
    filterCommandPalette();
}

function closeCommandPalette() {
    const modal = document.getElementById("command-palette-modal");
    if (modal) modal.style.display = "none";
}

function filterCommandPalette() {
    const query = (document.getElementById("cmd-palette-input")?.value || "").toLowerCase().trim();
    if (!query) {
        filteredCommands = COMMAND_REGISTRY.slice(0, 10);
    } else {
        filteredCommands = COMMAND_REGISTRY.filter(cmd => 
            cmd.title.toLowerCase().includes(query) || 
            cmd.category.toLowerCase().includes(query) ||
            cmd.shortcut.toLowerCase().includes(query)
        );
    }
    selectedCommandIndex = 0;
    renderCommandPaletteResults();
}

function renderCommandPaletteResults() {
    const container = document.getElementById("cmd-palette-results");
    if (!container) return;
    if (filteredCommands.length === 0) {
        container.innerHTML = `<div style="padding:16px; text-align:center; color:var(--text-muted); font-size:0.85rem;">No matching commands found.</div>`;
        return;
    }
    container.innerHTML = filteredCommands.map((cmd, idx) => `
        <div class="tf-cmd-item ${idx === selectedCommandIndex ? 'selected' : ''}" onclick="executeCommand(${idx})">
            <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; font-weight:600; width:90px;">${cmd.category}</span>
                <span style="font-weight:600;">${cmd.title}</span>
            </div>
            ${cmd.shortcut ? `<span class="tf-kbd-pill">${cmd.shortcut}</span>` : ''}
        </div>
    `).join("");
}

function executeCommand(index) {
    if (index >= 0 && index < filteredCommands.length) {
        const cmd = filteredCommands[index];
        closeCommandPalette();
        cmd.action();
    }
}

function handleCommandPaletteKey(e) {
    if (e.key === "Escape") {
        closeCommandPalette();
    } else if (e.key === "ArrowDown") {
        e.preventDefault();
        selectedCommandIndex = (selectedCommandIndex + 1) % filteredCommands.length;
        renderCommandPaletteResults();
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        selectedCommandIndex = (selectedCommandIndex - 1 + filteredCommands.length) % filteredCommands.length;
        renderCommandPaletteResults();
    } else if (e.key === "Enter") {
        e.preventDefault();
        executeCommand(selectedCommandIndex);
    }
}

// ---------------------------------------------------------------------------------------------------------------
// Sales / purchase invoice entry (invoice mode of the voucher screen): several item lines, extra ledgers, GST, bill reference.
// Every figure comes from the server's preview, so what you see is what saving posts.
// ---------------------------------------------------------------------------------------------------------------
const INVOICE_TYPES = ["Sales", "Purchase", "Credit Note", "Debit Note"];
let invoiceTimer = null;

function invoiceIsSalesSide(type) { return type === "Sales" || type === "Credit Note"; }

function invoiceFill(select, names, keep) {
    select.innerHTML = "";
    names.forEach(n => { const o = document.createElement("option"); o.value = o.textContent = n; select.appendChild(o); });
    if (keep && names.includes(keep)) select.value = keep;
}

function openInvoiceMode(type) {
    state.voucherMode = "invoice";
    const salesSide = invoiceIsSalesSide(type);
    const partyGroups = salesSide ? /Sundry Debtors|Cash-in-Hand|Bank/ : /Sundry Creditors|Cash-in-Hand|Bank/;
    const bookGroups = salesSide ? /Sales/ : /Purchase/;
    invoiceFill(document.getElementById("inv-party"), state.ledgers.filter(l => partyGroups.test(l.group_name || "")).map(l => l.name));
    invoiceFill(document.getElementById("inv-book"), state.ledgers.filter(l => bookGroups.test(l.group_name || "")).map(l => l.name));
    document.getElementById("inv-book-label").textContent = salesSide ? "Sales ledger" : "Purchase ledger";
    document.getElementById("inv-ref-label").firstChild.textContent = salesSide ? "Reference no. " : "Supplier invoice no. ";
    ["inv-reference", "inv-order", "inv-pos", "inv-due", "inv-shipped", "inv-destination", "inv-vehicle", "inv-consignee", "inv-narration"].forEach(id => { document.getElementById(id).value = ""; });
    document.getElementById("inv-gst").checked = false;
    document.getElementById("inv-round").checked = false;
    state.invoiceRows = [{item: "", godown: "", qty: "", rate: "", disc: ""}];
    state.invoiceCharges = [];
    document.getElementById("vch-standard-mode").style.display = "none";
    document.getElementById("vch-multiline-mode").style.display = "none";
    document.getElementById("vch-invoice-mode").style.display = "flex";
    const toggle = document.getElementById("vch-mode-toggle-btn");
    if (toggle) toggle.style.display = "none";
    invoiceRender();
    invoicePartyChanged();
}

function invoicePartyChanged() {
    const name = document.getElementById("inv-party").value;
    const ledger = state.ledgers.find(l => l.name === name) || {};
    const bits = [ledger.group_name, ledger.state ? "State: " + ledger.state : "", ledger.gstin ? "GSTIN: " + ledger.gstin : "",
                  ledger.credit_period_days ? "Credit period: " + ledger.credit_period_days + " days" : ""].filter(Boolean);
    document.getElementById("inv-party-info").textContent = bits.join(" | ");
    invoiceRecalcSoon();
}

function invoiceAddRow() { state.invoiceRows.push({item: "", godown: "", qty: "", rate: "", disc: ""}); invoiceRender(); }
function invoiceRemoveRow(i) { state.invoiceRows.splice(i, 1); if (!state.invoiceRows.length) invoiceAddRow(); else { invoiceRender(); invoiceRecalcSoon(); } }
function invoiceAddCharge() { state.invoiceCharges.push({ledger: "", amount: "", percent: "", side: ""}); invoiceRender(); }
function invoiceRemoveCharge(i) { state.invoiceCharges.splice(i, 1); invoiceRender(); invoiceRecalcSoon(); }

function invoiceRender() {
    const itemOptions = '<option value=""></option>' + state.stockItems.map(i =>
        `<option value="${printEsc(i.name)}">${printEsc(i.name)} (${printEsc(i.closing_qty || 0)} ${printEsc(i.uom_symbol || "")})</option>`).join("");
    const godownNames = (state.godowns || []).map(g => g.name).filter(Boolean);
    const godownOptions = (godownNames.length ? godownNames : ["Main Location"]).map(n => `<option value="${printEsc(n)}">${printEsc(n)}</option>`).join("");
    const body = document.getElementById("inv-items-body");
    body.innerHTML = "";
    state.invoiceRows.forEach((r, i) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td><select class="tally-input" style="width:100%;" data-f="item">${itemOptions}</select></td>
            <td><select class="tally-input" style="width:100%;" data-f="godown">${godownOptions}</select></td>
            <td><input class="tally-input" style="width:100%; text-align:right;" data-f="qty"></td>
            <td><input class="tally-input" style="width:100%; text-align:right;" data-f="rate"></td>
            <td><input class="tally-input" style="width:100%; text-align:right;" data-f="disc"></td>
            <td style="text-align:right;" data-amount="${i}">${r.amount !== undefined ? printMoney(r.amount) : ""}</td>
            <td><span style="cursor:pointer;" title="Remove line">&#10005;</span></td>`;
        tr.querySelectorAll("[data-f]").forEach(el => {
            const f = el.dataset.f;
            el.value = r[f] || (f === "godown" ? godownNames[0] || "Main Location" : "");
            if (f === "godown" && !r.godown) r.godown = el.value;
            el.oninput = el.onchange = () => { r[f] = el.value; invoiceRecalcSoon(); };
        });
        tr.lastElementChild.onclick = () => invoiceRemoveRow(i);
        body.appendChild(tr);
    });
    const ledgerNames = state.ledgers.map(l => l.name);
    const cbody = document.getElementById("inv-charges-body");
    cbody.innerHTML = "";
    state.invoiceCharges.forEach((c, i) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td><select class="tally-input" style="width:100%;" data-f="ledger"><option value=""></option>${ledgerNames.map(n => `<option value="${printEsc(n)}">${printEsc(n)}</option>`).join("")}</select></td>
            <td><input class="tally-input" style="width:100%; text-align:right;" data-f="amount"></td>
            <td><input class="tally-input" style="width:100%; text-align:right;" data-f="percent"></td>
            <td><select class="tally-input" style="width:100%;" data-f="side"><option value="">as invoice</option><option value="Dr">Dr</option><option value="Cr">Cr</option></select></td>
            <td><span style="cursor:pointer;" title="Remove">&#10005;</span></td>`;
        tr.querySelectorAll("[data-f]").forEach(el => {
            const f = el.dataset.f;
            el.value = c[f] || "";
            el.oninput = el.onchange = () => { c[f] = el.value; invoiceRecalcSoon(); };
        });
        tr.lastElementChild.onclick = () => invoiceRemoveCharge(i);
        cbody.appendChild(tr);
    });
}

function invoicePayload() {
    const get = id => document.getElementById(id).value.trim();
    const items = state.invoiceRows.filter(r => r.item).map(r => ({
        stock_item_name: r.item, godown_name: r.godown || undefined, qty: parseFloat(r.qty) || 0, rate: parseFloat(r.rate) || 0,
        discount_percent: parseFloat(r.disc) || 0}));
    const charges = state.invoiceCharges.filter(c => c.ledger && (parseFloat(c.amount) > 0 || parseFloat(c.percent) > 0)).map(c => ({
        ledger_name: c.ledger, amount: parseFloat(c.amount) || 0, percent: parseFloat(c.percent) || undefined, entry_type: c.side || undefined}));
    const book = get("inv-book");
    return {voucher_type: state.voucherType, date: document.getElementById("vch-date").value, party_ledger: get("inv-party"),
            sales_ledger: book, items, charges, gst: document.getElementById("inv-gst").checked, round_off: document.getElementById("inv-round").checked,
            place_of_supply: get("inv-pos") || undefined, due_date: get("inv-due") || undefined, reference_no: get("inv-reference") || undefined,
            order_ref: get("inv-order") || undefined, shipped_by: get("inv-shipped") || undefined, destination: get("inv-destination") || undefined,
            vehicle_no: get("inv-vehicle") || undefined, consignee_name: get("inv-consignee") || undefined, narration: get("inv-narration")};
}

function invoiceRecalcSoon() { clearTimeout(invoiceTimer); invoiceTimer = setTimeout(invoiceRecalc, 300); }

async function invoiceRecalc() {
    const totals = document.getElementById("inv-totals");
    const messages = document.getElementById("inv-messages");
    const payload = invoicePayload();
    if (!payload.items.length) { totals.textContent = ""; messages.textContent = ""; return; }
    try {
        const res = await fetch("/api/invoices/compute", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
        const out = await res.json();
        if (!res.ok) { messages.textContent = out.error || "Cannot calculate this invoice yet"; return; }
        messages.textContent = (out.warnings || []).join(" ");
        document.querySelectorAll("[data-amount]").forEach(td => td.textContent = "");
        let n = 0;
        state.invoiceRows.forEach((r, i) => {
            if (!r.item) { r.amount = undefined; return; }
            r.amount = out.items[n] ? out.items[n].amount : undefined;
            const td = document.querySelector(`[data-amount="${i}"]`);
            if (td && r.amount !== undefined) td.textContent = printMoney(r.amount);
            n++;
        });
        const line = (label, value) => `<div style="display:flex; justify-content:space-between;"><span>${printEsc(label)}</span><strong>${printMoney(value)}</strong></div>`;
        let html = line("Items", out.item_total);
        out.charges.forEach(c => { html += line(`${c.ledger_name} (${c.entry_type})`, c.amount); });
        out.tax_lines.forEach(t => { html += line(`${t.kind} ${t.rate}%`, t.amount); });
        html += `<div style="display:flex; justify-content:space-between; font-size:1rem; margin-top:4px;"><span>Invoice total (${printEsc(out.voucher_number)})</span><strong>&#8377; ${printMoney(out.total)}</strong></div>`;
        totals.innerHTML = html;
    } catch (err) {
        messages.textContent = "Could not reach the server: " + err;
    }
}

async function invoiceSave() {
    const payload = invoicePayload();
    if (!payload.items.length) { alert("Add at least one item line."); return; }
    try {
        const res = await fetch("/api/invoices/save", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
        const out = await res.json();
        if (!res.ok) { alert(`Voucher Error: ${out.error}`); return; }
        alert(`\u2713 Invoice ${out.voucher_number} accepted (voucher ${out.voucher_id}). Total \u20B9${printMoney(out.total)}.`);
        state.lastInvoiceId = out.voucher_id;
        await loadAppState();
        goBackToGateway();
    } catch (err) {
        alert("Failed to save the invoice: " + err);
    }
}

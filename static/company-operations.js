async function companyOperation(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Operation failed');
    return result;
}
function operationsMessage(value) { document.getElementById('operations-status').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
function operationOptions(id, values) {
    const el = document.getElementById(id); el.replaceChildren();
    values.forEach(([value,label]) => { const option = document.createElement('option'); option.value = value; option.textContent = label; el.appendChild(option); });
}
async function openCompanyOperations() {
    if (!companyReady) return;
    document.getElementById('company-operations-modal').style.display = 'flex';
    try {
        const [schedule, capabilities, registrations] = await Promise.all([
            companyOperation('/api/backup/schedule'), companyOperation('/api/system/capabilities'), companyOperation('/api/gst/registrations')]);
        document.getElementById('backup-enabled').checked = schedule.schedule.enabled;
        document.getElementById('backup-interval').value = schedule.schedule.interval_minutes;
        document.getElementById('backup-keep').value = schedule.schedule.keep;
        operationOptions('valuation-item', state.stockItems.map(x => [x.name,x.name]));
        ['expense','payment','deductions'].forEach(k => operationOptions('payroll-'+k, state.ledgers.map(x=>[x.name,x.name])));
        operationOptions('gst-books-registration', [['all','All registrations'],['main','Main registration'],...registrations.map(r=>[String(r.id),r.name+' · '+r.gstin])]);
        if(!document.getElementById('payroll-period').value){const now=new Date();document.getElementById('payroll-period').value=now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');}
        if(!document.getElementById('payroll-posting-date').value){const now=new Date();document.getElementById('payroll-posting-date').value=now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0')+'-'+String(now.getDate()).padStart(2,'0');}
        if (!document.getElementById('gst-books-period').value) {const now=new Date();document.getElementById('gst-books-period').value=now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');}
        document.getElementById('registration-list').textContent = registrations.map(x=>`${x.name}: ${x.gstin}`).join('\n') || 'No additional registrations';
        const list = document.getElementById('capability-list'); list.replaceChildren();
        Object.entries(capabilities.requirements).forEach(([name,requirement]) => {
            const row = document.createElement('li'); row.textContent = name.replaceAll('_',' ') + ': ' + requirement; list.appendChild(row);
        });
        await loadConsolidationCompanies();
        operationsMessage('Company tools ready. Connected services require the account setup listed below.');
    } catch(err) {operationsMessage(err.message);}
}
async function saveBackupSchedule() {
    try {await companyOperation('/api/backup/schedule', {enabled:document.getElementById('backup-enabled').checked, interval_minutes:Number(document.getElementById('backup-interval').value), keep:Number(document.getElementById('backup-keep').value)}); operationsMessage('Backup schedule saved. While the app is closed, an OS scheduled worker must be installed.');}
    catch(err) {operationsMessage(err.message);}
}
async function showBackupVersions() {try {operationsMessage(await companyOperation('/api/backup/versions'));} catch(err){operationsMessage(err.message);}}
async function saveValuation() {
    try {await companyOperation('/api/inventory/valuation',{item:document.getElementById('valuation-item').value,method:document.getElementById('valuation-method').value,standard_cost:Number(document.getElementById('valuation-standard').value)}); await loadAppState(); operationsMessage('Inventory valuation saved.');}
    catch(err){operationsMessage(err.message);}
}
async function savePayrollAccounts() {
    try {await companyOperation('/api/payroll/config', Object.fromEntries(['expense','payment','deductions'].map(k=>[k+'_ledger',document.getElementById('payroll-'+k).value]))); operationsMessage('Payroll accounts saved.');} catch(err){operationsMessage(err.message);}
}
async function saveRegistration() {
    try {const data = Object.fromEntries(['name','gstin','state_code','address','pincode'].map(k=>[k,document.getElementById('registration-'+k).value])); await companyOperation('/api/gst/registrations',data); document.getElementById('registration-list').textContent = (await companyOperation('/api/gst/registrations')).map(x=>`${x.name}: ${x.gstin}`).join('\n'); operationsMessage('GST registration saved.');} catch(err){operationsMessage(err.message);}
}
let workbookImport = null;
async function prepareWorkbook() {
    try {
        const file = document.getElementById('excel-file').files[0]; if (!file) throw new Error('Choose an XLSX file');
        const bytes = new Uint8Array(await file.arrayBuffer());
        if (bytes.length > 10*1024*1024) throw new Error('Workbook exceeds 10 MB');
        let binary = ''; for (let offset=0;offset<bytes.length;offset+=8192) binary += String.fromCharCode(...bytes.subarray(offset,offset+8192));
        workbookImport = {content_base64:btoa(binary)};
        // Read headers first through the preview endpoint; the name mapping is chosen below.
        const result = await companyOperation('/api/import/excel/headers', workbookImport);
        ['name','group_name','opening_balance','opening_type','uom_symbol','opening_qty','opening_rate'].forEach(k=>operationOptions('excel-'+k, [['','Skip'],...result.headers.map(h=>[h,h])]));
        operationsMessage(`${result.row_count} rows found. Map columns, then preview before importing.`);
    } catch(err){operationsMessage(err.message);}
}
async function runWorkbookImport(apply=false) {
    try {
        if (!workbookImport) throw new Error('Read an XLSX file first');
        const kind = document.getElementById('excel-kind').value;
        const fields = kind==='Ledgers' ? ['name','group_name','opening_balance','opening_type'] : ['name','group_name','uom_symbol','opening_qty','opening_rate'];
        const mapping = Object.fromEntries(fields.map(k=>[k,document.getElementById('excel-'+k).value]).filter(x=>x[1]));
        if (apply && !confirm('Import these rows into the active company?')) return;
        operationsMessage(await companyOperation('/api/import/excel',{...workbookImport,kind,mapping,mapping_name:document.getElementById('excel-mapping-name').value || 'Default',preview:!apply}));
        if(apply) await loadAppState();
    } catch(err){operationsMessage(err.message);}
}
async function loadIMSFile() {
    try {const file = document.getElementById('ims-file').files[0];if(!file) throw new Error('Choose an IMS JSON file'); const invoices = JSON.parse(await file.text());operationsMessage(await companyOperation('/api/gst/ims/import',{period:document.getElementById('ims-period').value,invoices})); await showIMS();} catch(err){operationsMessage(err.message);}
}
async function showIMS() {
    try {
        const period = document.getElementById('ims-period').value;
        const result = await companyOperation('/api/gst/ims?period='+encodeURIComponent(period));
        const list = document.getElementById('ims-list'); list.replaceChildren();
        result.invoices.forEach(invoice=>{
            const row=document.createElement('div');row.style.padding='8px';
            const label=document.createElement('span');label.textContent=`${invoice.supplier_gstin} · ${invoice.invoice_number} · Tax ${invoice.tax_value} · ${invoice.action} `;row.appendChild(label);
            const select=document.createElement('select');['Accepted','Rejected','Pending'].forEach(action=>{const o=document.createElement('option');o.value=action;o.textContent=action;o.selected=invoice.action===action;select.appendChild(o);});row.appendChild(select);
            const remarks=document.createElement('input');remarks.placeholder='Remarks';remarks.value=invoice.remarks;row.appendChild(remarks);
            const save=document.createElement('button');save.textContent='Save locally';save.onclick=async()=>{try{operationsMessage(await companyOperation('/api/gst/ims/action',{invoice_id:invoice.id,action:select.value,remarks:remarks.value}));}catch(err){operationsMessage(err.message);}};row.appendChild(save);list.appendChild(row);
        });
        operationsMessage(`Accepted invoice tax: ${result.accepted_tax}. Decisions are saved locally; no GST portal submission has occurred.`);
    } catch(err){operationsMessage(err.message);}
}
async function exportEncryptedArchive() {
    try {const password=document.getElementById('archive-password').value;operationsMessage(await companyOperation('/api/archive/export',{password}));document.getElementById('archive-password').value='';} catch(err){operationsMessage(err.message);}
}

async function searchCompanyBooks() {
    try {
        const result=await companyOperation('/api/search/companies?q='+encodeURIComponent(document.getElementById('company-search-query').value));
        const list=document.getElementById('company-search-results');list.replaceChildren();
        result.results.forEach(item=>{const button=document.createElement('button');button.style.display='block';button.textContent=`${item.company_name}: ${item.kind} · ${item.label}`;button.onclick=async()=>{await selectStoredCompany(item.storage_key);document.getElementById('company-operations-modal').style.display='none';};list.appendChild(button);});
        operationsMessage(`${result.results.length} results. ${result.skipped_protected} protected companies require separate sign-in.`);
    } catch(err){operationsMessage(err.message);}
}

async function downloadWorkbook() {
    try {const kind=document.getElementById('export-kind').value;const response=await fetch('/api/export/excel?kind='+encodeURIComponent(kind));if(!response.ok) throw new Error('Excel export failed'); const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=kind.replaceAll(' ','_')+'.xlsx';a.click();URL.revokeObjectURL(url);} catch(err){operationsMessage(err.message);}
}
async function emailWorkbook() {
    try {const recipient=document.getElementById('export-recipient').value.trim(),kind=document.getElementById('export-kind').value;if(!recipient || !confirm(`Send ${kind} from the active company to ${recipient}?`)) return;operationsMessage(await companyOperation('/api/share/email',{recipient,kind,confirm_send:true}));} catch(err){operationsMessage(err.message);}
}

async function populateGSTRegistrationChoices() {
    try {const result=await companyOperation('/api/gst/registrations');operationOptions('vch-gst-registration',[['','Main registration'],...result.map(r=>[r.id,r.name+' · '+r.gstin])]);} catch(err){console.error(err);}
}

function appendReportTable(container, title, headers, rows) {
    const heading=document.createElement('h3');heading.textContent=title;container.appendChild(heading);
    const wrapper=document.createElement('div');wrapper.style.overflowX='auto';
    const table=document.createElement('table');table.style.cssText='width:100%;border-collapse:collapse;margin:12px 0;';
    const head=document.createElement('thead'),headRow=document.createElement('tr');
    headers.forEach(label=>{const cell=document.createElement('th');cell.textContent=label;cell.style.cssText='text-align:left;padding:8px;border-bottom:1px solid var(--border-color);';headRow.appendChild(cell);});head.appendChild(headRow);table.appendChild(head);
    const body=document.createElement('tbody');rows.forEach(values=>{const row=document.createElement('tr');values.forEach(value=>{const cell=document.createElement('td');cell.textContent=String(value??'');cell.style.cssText='padding:8px;border-bottom:1px solid var(--border-color);';row.appendChild(cell);});body.appendChild(row);});table.appendChild(body);wrapper.appendChild(table);container.appendChild(wrapper);
}
function reportMoney(value) {return Number(value||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});}
async function fetchGSTBooks() {
    return companyOperation('/api/gst/books?period='+encodeURIComponent(document.getElementById('gst-books-period').value)+'&registration='+encodeURIComponent(document.getElementById('gst-books-registration').value));
}
async function showGSTBookReport() {
    try {
        const report=await fetchGSTBooks(),container=document.getElementById('gst-books-report');container.replaceChildren();
        const note=document.createElement('p');note.textContent=report.note;container.appendChild(note);
        appendReportTable(container,report.registration_name+' · '+report.period,['Direction','Document value','Taxable value','CGST','SGST','IGST','Cess'],Object.entries(report.totals).map(([direction,t])=>[direction,...['document_value','taxable_value','cgst','sgst','igst','cess'].map(k=>reportMoney(t[k]))]));
        appendReportTable(container,'Invoices',['Date','Number','Direction','Party','GSTIN','Taxable value','Posted tax'],report.invoices.map(i=>[i.date,i.number,i.direction,i.party,i.party_gstin||'Unregistered',reportMoney(i.taxable_value),reportMoney(Object.values(i.tax).reduce((a,b)=>a+Number(b),0))]));
        if(report.warnings.length) appendReportTable(container,'Review warnings',['Invoice','Issue'],report.warnings.map(w=>[w.number,w.message]));
        if(report.excluded_documents.length) appendReportTable(container,'Excluded documents',['Document','Reason'],report.excluded_documents.map(d=>[d.number,d.reason]));
        operationsMessage(`${report.invoices.length} invoices · ${report.warnings.length} review warnings · ${report.excluded_documents.length} excluded notes. No portal filing has occurred.`);
    } catch(err){operationsMessage(err.message);}
}
async function downloadGSTBookReport() {
    try {const report=await fetchGSTBooks();const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='GST_Book_Review_'+report.period+'.json';link.click();URL.revokeObjectURL(url);}catch(err){operationsMessage(err.message);}
}

let consolidationCompanies=[],consolidationGroups=[],consolidationEliminations=[],consolidationOwnerContext=null;
function memberAccess() {
    try {return JSON.parse(sessionStorage.getItem('tf_member_access')||'{}');} catch{return {};}
}
function selectedConsolidationKeys() {return Array.from(document.querySelectorAll('#consolidation-members input:checked')).map(el=>el.value);}
function consolidationPayload() {
    const id=document.getElementById('consolidation-saved').value;
    return {id:id||undefined,name:document.getElementById('consolidation-name').value,storage_keys:selectedConsolidationKeys(),eliminations:consolidationEliminations,authorizations:memberAccess()};
}
async function loadConsolidationCompanies() {
    const [catalogue,groups]=await Promise.all([companyOperation('/api/storage'),companyOperation('/api/company/consolidation/groups')]);
    const changed=consolidationOwnerContext!==companyContext;
    const previous=new Set(changed?[]:selectedConsolidationKeys());consolidationCompanies=catalogue.companies;consolidationGroups=groups;
    if(changed){consolidationEliminations=[];document.getElementById('consolidation-name').value='';renderConsolidationEliminations();consolidationOwnerContext=companyContext;}
    const container=document.getElementById('consolidation-members');container.replaceChildren();
    consolidationCompanies.forEach(company=>{
        const label=document.createElement('label');label.style.display='block';const check=document.createElement('input');check.type='checkbox';check.value=company.storage_key;check.checked=previous.has(company.storage_key);check.onchange=updateEliminationCompanies;label.appendChild(check);
        const text=document.createElement('span');text.textContent=' '+company.company_number+' · '+company.name+(company.security_enabled?' (protected)':'');label.appendChild(text);container.appendChild(label);
    });
    operationOptions('consolidation-saved',[['','New group'],...groups.map(g=>[g.id,g.name])]);
    operationOptions('consolidation-auth-member',consolidationCompanies.filter(c=>c.security_enabled).map(c=>[c.storage_key,c.name]));
    updateEliminationCompanies();
}
function updateEliminationCompanies() {
    const keys=new Set(selectedConsolidationKeys()),options=consolidationCompanies.filter(c=>keys.has(c.storage_key)).map(c=>[c.storage_key,c.name]);
    ['debit','credit'].forEach(side=>{
        const select=document.getElementById('elimination-'+side+'-company'),previous=select.value;
        operationOptions(select.id,[['','Choose a member'],...options]);if(keys.has(previous))select.value=previous;
        operationOptions('elimination-'+side+'-ledger',[]);
    });
}
async function authorizeConsolidationMember() {
    try {
        const key=document.getElementById('consolidation-auth-member').value;
        const result=await companyOperation('/api/company/consolidation/authorize',{storage_key:key,username:document.getElementById('consolidation-username').value,password:document.getElementById('consolidation-password').value});
        const access=memberAccess();access[key]={context:result.context,token:result.token};sessionStorage.setItem('tf_member_access',JSON.stringify(access));document.getElementById('consolidation-password').value='';operationsMessage('Member reporting access granted for this session.');
    } catch(err){operationsMessage(err.message);}
}
async function loadEliminationLedgers(side) {
    try {
        const key=document.getElementById('elimination-'+side+'-company').value;
        if(!key){operationOptions('elimination-'+side+'-ledger',[]);return;}
        const result=await companyOperation('/api/company/consolidation/ledgers',{storage_key:key,authorizations:memberAccess()});
        operationOptions('elimination-'+side+'-ledger',result.filter(r=>Number(side==='debit'?r.dr_amount:r.cr_amount)>0).map(r=>[r.name,`${r.name} · ${r.group_name} · ${reportMoney(side==='debit'?r.dr_amount:r.cr_amount)}`]));
    }catch(err){operationsMessage(err.message);}
}
function renderConsolidationEliminations() {
    const container=document.getElementById('consolidation-eliminations');container.replaceChildren();
    consolidationEliminations.forEach((entry,index)=>{const row=document.createElement('div'),text=document.createElement('span');text.textContent=`${entry.reference}: ${entry.debit_ledger} / ${entry.credit_ledger} · ${reportMoney(entry.amount)} `;row.appendChild(text);const button=document.createElement('button');button.textContent='Remove';button.onclick=()=>{consolidationEliminations.splice(index,1);renderConsolidationEliminations();};row.appendChild(button);container.appendChild(row);});
}
function addConsolidationElimination() {
    const value=id=>document.getElementById(id).value;
    const entry={kind:value('elimination-kind'),debit_company:value('elimination-debit-company'),debit_ledger:value('elimination-debit-ledger'),credit_company:value('elimination-credit-company'),credit_ledger:value('elimination-credit-ledger'),amount:value('elimination-amount'),reference:value('elimination-reference').trim()};
    if(!entry.debit_company||!entry.credit_company||!entry.debit_ledger||!entry.credit_ledger||!entry.reference||Number(entry.amount)<=0){operationsMessage('Choose both member ledgers, a positive amount and a reference.');return;}
    consolidationEliminations.push(entry);renderConsolidationEliminations();
}
function loadSavedConsolidation() {
    const id=document.getElementById('consolidation-saved').value,group=consolidationGroups.find(g=>g.id===id);
    document.getElementById('consolidation-name').value=group?.name||'';
    const keys=new Set(group?.storage_keys||[]);document.querySelectorAll('#consolidation-members input').forEach(input=>input.checked=keys.has(input.value));
    consolidationEliminations=group?JSON.parse(JSON.stringify(group.eliminations)):[];updateEliminationCompanies();renderConsolidationEliminations();
}
async function saveConsolidationGroup() {
    try {const result=await companyOperation('/api/company/consolidation/groups',consolidationPayload());await loadConsolidationCompanies();document.getElementById('consolidation-saved').value=result.id;loadSavedConsolidation();operationsMessage('Group definition saved. No source vouchers were changed.');}catch(err){operationsMessage(err.message);}
}
async function deleteConsolidationGroup() {
    try {const id=document.getElementById('consolidation-saved').value;if(!id||!confirm('Delete this saved group definition? Source company books will remain available.'))return;await companyOperation('/api/company/consolidation/groups/delete',{id});await loadConsolidationCompanies();loadSavedConsolidation();operationsMessage('Saved group definition deleted.');}catch(err){operationsMessage(err.message);}
}
async function runConsolidation() {
    try {
        const report=await companyOperation('/api/company/consolidated',consolidationPayload()),container=document.getElementById('consolidation-report');container.replaceChildren();
        const note=document.createElement('p');note.textContent=report.note;container.appendChild(note);
        appendReportTable(container,'Consolidated totals · '+report.currency,['Measure','Before adjustments','After adjustments'],Object.keys(report.totals).map(k=>[k.replaceAll('_',' '),reportMoney(report.before_eliminations[k]),reportMoney(report.totals[k])]));
        appendReportTable(container,'Trial balance',['Ledger','Group','Debit','Credit'],report.trial_balance.map(r=>[r.name,r.group_name,reportMoney(r.debit),reportMoney(r.credit)]));
        appendReportTable(container,'Elimination journal (report only)',['Company','Ledger','Entry','Amount','Reference'],report.elimination_journal.map(r=>[r.company,r.ledger,r.entry_type,reportMoney(r.amount),r.reference]));
        operationsMessage(`Group report ready. Trial balance difference: ${reportMoney(report.trial_totals.difference)}. Source books were unchanged.`);
    }catch(err){operationsMessage(err.message);}
}

let monthlyPayroll=null,payrollOwnerContext=null,payrollPreviewId=null,payrollPreviewPeriod=null,employeeDeductions={};
const payrollFields={scheduled_days:'scheduled-days',paid_days:'paid-days',overtime_hours:'overtime-hours',overtime_rate:'overtime-rate',arrears:'arrears',bonus:'bonus',other_deductions:'other-deductions',reference:'input-reference'};
function selectedPayrollPeriod() {const value=document.getElementById('payroll-period').value;if(!value)throw new Error('Choose a payroll month');return value;}
function clearPayrollPreview() {payrollPreviewId=null;payrollPreviewPeriod=null;}
async function loadMonthlyPayroll() {
    try {
        clearPayrollPreview();monthlyPayroll=await companyOperation('/api/payroll/month?period='+encodeURIComponent(selectedPayrollPeriod()));payrollOwnerContext=companyContext;
        operationOptions('payroll-month-employee',monthlyPayroll.employees.map(e=>[e.emp_code,e.emp_code+' · '+e.name]));
        document.getElementById('payroll-month-inputs').disabled=monthlyPayroll.finalised;
        document.getElementById('payroll-input-note').textContent=monthlyPayroll.finalised?'This month is finalised. Use View saved payslips.':monthlyPayroll.note;
        showPayrollEmployeeInput();
    } catch(err){operationsMessage(err.message);}
}
function showPayrollEmployeeInput() {
    const employee=monthlyPayroll?.employees.find(e=>e.emp_code===document.getElementById('payroll-month-employee').value);
    if(!employee)return;
    Object.entries(payrollFields).forEach(([key,id])=>document.getElementById('payroll-'+id).value=employee.inputs[key]??'');
    employeeDeductions={...employee.inputs.deductions};renderPayrollDeductions();
    if(employee.inputs.legacy_attendance && !employee.inputs.saved)document.getElementById('payroll-input-note').textContent='Older attendance data exists. Review it and save the monthly paid-day basis before using attendance-based pay.';
}
function renderPayrollDeductions() {
    const container=document.getElementById('payroll-manual-deductions');container.replaceChildren();
    Object.entries(employeeDeductions).forEach(([name,value])=>{
        const row=document.createElement('label');row.style.display='block';const text=document.createElement('span');text.textContent=name+' ';row.appendChild(text);
        const input=document.createElement('input');input.type='number';input.min='0';input.step='0.01';input.value=value;input.oninput=()=>employeeDeductions[name]=input.value;row.appendChild(input);
        const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>{delete employeeDeductions[name];renderPayrollDeductions();};row.appendChild(remove);container.appendChild(row);
    });
}
function addPayrollDeduction() {
    const name=document.getElementById('payroll-new-deduction-name').value.trim(),amount=document.getElementById('payroll-new-deduction-amount').value;
    if(!name||!amount||!Number.isFinite(Number(amount))||Number(amount)<0){operationsMessage('Enter a deduction name and a non-negative amount.');return;}
    employeeDeductions[name]=amount;renderPayrollDeductions();document.getElementById('payroll-new-deduction-name').value='';document.getElementById('payroll-new-deduction-amount').value='';
}
async function saveMonthlyPayrollInput() {
    try {
        if(payrollOwnerContext!==companyContext||monthlyPayroll?.period!==selectedPayrollPeriod())throw new Error('Load employees for the selected month before saving.');
        const emp_code=document.getElementById('payroll-month-employee').value;
        const values=Object.fromEntries(Object.entries(payrollFields).map(([key,id])=>[key,document.getElementById('payroll-'+id).value]));
        await companyOperation('/api/payroll/month',{period:selectedPayrollPeriod(),emp_code,...values,deductions:employeeDeductions});
        clearPayrollPreview();await loadMonthlyPayroll();document.getElementById('payroll-month-employee').value=emp_code;showPayrollEmployeeInput();operationsMessage('Employee inputs saved. Refresh the month preview before posting.');
    }catch(err){operationsMessage(err.message);}
}
function renderMonthlyPayroll(report) {
    const container=document.getElementById('payroll-month-report');container.replaceChildren();
    const note=document.createElement('p');note.textContent=report.note;container.appendChild(note);
    appendReportTable(container,(report.finalised?'Saved payroll':'Payroll preview')+' · '+report.period,['Employee','Gross','Deductions','Net','Deduction basis'],report.payslips.map(s=>[s.emp_code+' · '+s.name,reportMoney(s.gross_salary),reportMoney(s.total_deductions),reportMoney(s.net_salary),s.deduction_basis||'Historical snapshot']));
    appendReportTable(container,'Totals',['Gross','Deductions','Net'],[[reportMoney(report.totals.gross_salary),reportMoney(report.totals.total_deductions),reportMoney(report.totals.net_salary)]]);
    report.payslips.forEach(slip=>{
        const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Payslip · '+slip.emp_code+' · '+slip.name;details.appendChild(summary);
        const account=document.createElement('p'),number=String(slip.account_no||'');account.textContent='Account: '+(number?'••••'+number.slice(-4):'Not recorded')+(report.payment_date?' · Posted '+report.payment_date:'');details.appendChild(account);
        appendReportTable(details,'Earnings',['Pay head','Amount'],Object.entries(slip.earnings).map(([name,value])=>[name,reportMoney(value)]));
        appendReportTable(details,'Deductions',['Deduction','Amount'],Object.entries(slip.deductions).map(([name,value])=>[name,reportMoney(value)]));
        const warnings=document.createElement('p');warnings.textContent=(slip.warnings||[]).join(' ');details.appendChild(warnings);
        if(report.finalised){const button=document.createElement('button');button.textContent='Print saved payslip';button.onclick=()=>printSavedPayslip(report,slip);details.appendChild(button);}
        container.appendChild(details);
    });
}
async function previewMonthlyPayroll() {
    try {
        const period=selectedPayrollPeriod(),report=await companyOperation('/api/payroll/preview?period='+encodeURIComponent(period));payrollOwnerContext=companyContext;
        clearPayrollPreview();if(!report.finalised){payrollPreviewId=report.preview_id;payrollPreviewPeriod=period;}
        renderMonthlyPayroll(report);operationsMessage(report.finalised?'This month already has a saved payroll run.':'Preview ready. Review all employees before posting.');
    }catch(err){clearPayrollPreview();operationsMessage(err.message);}
}
async function postMonthlyPayroll() {
    try {
        const period=selectedPayrollPeriod(),date=document.getElementById('payroll-posting-date').value;
        if(payrollOwnerContext!==companyContext||!payrollPreviewId||payrollPreviewPeriod!==period)throw new Error('Preview the selected month before posting.');
        if(!date)throw new Error('Choose a payment / posting date.');
        if(!confirm('Post the reviewed payroll for '+period+' on '+date+'?'))return;
        const result=await companyOperation('/api/payroll/process',{month:period,date,preview_id:payrollPreviewId});clearPayrollPreview();
        await loadMonthlyPayroll();await loadPostedPayroll();await loadAppState();operationsMessage('Payroll posted once. Voucher '+result.voucher_id+'. Saved payslips are available.');
    }catch(err){clearPayrollPreview();operationsMessage(err.message);}
}
async function loadPostedPayroll() {
    try {const report=await companyOperation('/api/payroll/run?period='+encodeURIComponent(selectedPayrollPeriod()));renderMonthlyPayroll(report);operationsMessage('Saved payroll status: '+report.status);}catch(err){operationsMessage(err.message);}
}
async function downloadPostedPayroll() {
    try {const report=await companyOperation('/api/payroll/run?period='+encodeURIComponent(selectedPayrollPeriod()));const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='Payroll_'+report.period+'.json';link.click();URL.revokeObjectURL(url);}catch(err){operationsMessage(err.message);}
}

function printSavedPayslip(report,slip) {
    const popup=window.open('','_blank','width=820,height=1000');
    if(!popup){operationsMessage('Allow a print window to print the saved payslip.');return;}
    const doc=popup.document;doc.title='Payslip '+report.period+' '+slip.emp_code;
    doc.body.replaceChildren();
    const style=doc.createElement('style');style.textContent='body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;color:#111}table{width:100%;border-collapse:collapse}td,th{padding:9px;text-align:left;border-bottom:1px solid #ddd}h1{font-size:24px}p{line-height:1.5}@media print{button{display:none}body{margin:0}}';doc.head.appendChild(style);
    const add=(tag,text)=>{const el=doc.createElement(tag);el.textContent=text;doc.body.appendChild(el);return el;};
    add('h1',slip.employer?.name||'Company payroll');if(slip.employer?.address)add('p',slip.employer.address);
    add('h2','Saved payslip · '+report.period);
    add('p',slip.emp_code+' · '+slip.name+' · '+(slip.designation||''));
    const account=String(slip.account_no||'');add('p','Posted '+(report.payment_date||'Unknown')+' · Account '+(account?'••••'+account.slice(-4):'Not recorded')+' · Status '+report.status);
    const section=(title,values)=>{add('h3',title);const table=doc.createElement('table');Object.entries(values).forEach(([name,amount])=>{const row=doc.createElement('tr');const label=doc.createElement('td');label.textContent=name;const value=doc.createElement('td');value.textContent=reportMoney(amount);row.append(label,value);table.appendChild(row);});doc.body.appendChild(table);};
    section('Earnings',slip.earnings);section('Deductions',slip.deductions);section('Totals',{'Gross salary':slip.gross_salary,'Total deductions':slip.total_deductions,'Net salary':slip.net_salary});
    add('p',slip.deduction_basis||'Historical snapshot');add('p',(slip.warnings||[]).join(' '));add('p','Payroll statement only. This is not Form 16 or an income-tax certificate.');
    const print=add('button','Print');print.onclick=()=>popup.print();popup.focus();
}

/* Contract tests for startup selection, masked login and request context headers. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map(), callbacks = {}, requests = [];
let catalogueMode = 'native';
let nativeReaderAvailable = true;
function mockElement(id='') {return {id,children:[],style:{},value:'',textContent:'',checked:false,classList:{add(){},remove(){}},replaceChildren(){this.children=[];},appendChild(c){this.children.push(c);},addEventListener(){}};}
const memory = () => {const values = new Map();return {getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};};
function el(id) {
    if (!elements.has(id)) elements.set(id,mockElement(id));
    return elements.get(id);
}
const context = {
    console, Headers, setTimeout, clearTimeout,
    sessionStorage:memory(),localStorage:memory(),confirm:()=>true,
    prompt:()=>{throw new Error('Company credentials must use masked form fields');},
    document:{getElementById:el,createElement:()=>mockElement(),addEventListener:(name,fn)=>callbacks[name]=fn,querySelectorAll:()=>[],documentElement:{setAttribute(){}}},
    window:{alert(){},addEventListener(){},fetch:async(url,options={})=>{
        requests.push({url,options});
        let data = {data_path:'/tmp/Companies',companies:[],native_companies:[{storage_key:'native:100124',company_number:'100124',name:'Native <&> company',format:'Tally binary .1800'}],errors:[]},status=200;
        if (catalogueMode === 'outdated') delete data.native_companies;
        if (catalogueMode === 'empty') data.native_companies = [];
        if(url==='/api/storage/select') {
            const body=JSON.parse(options.body);
            if(body.password==='correct') data={company_context:'new-window-context',token:'authenticated-token'};
            else {status=401;data={error:'Company username and password required'};}
        }
        if(url.startsWith('/api/gst/books?')) data={period:'2026-04',registration_name:'Main registration',note:'Book review',totals:{Outward:{document_value:105,taxable_value:100,cgst:2.5,sgst:2.5,igst:0,cess:0}},invoices:[{date:'2026-04-30',number:'S-1',direction:'Outward',party:'<unsafe party>',party_gstin:'',taxable_value:100,tax:{cgst:2.5,sgst:2.5}}],warnings:[],excluded_documents:[]};
        if(url.startsWith('/api/payroll/preview?'))data={period:'2026-04',preview_id:'reviewed-fingerprint',finalised:false,note:'Manual deductions',totals:{gross_salary:1000,total_deductions:100,net_salary:900},payslips:[{emp_code:'E1',name:'<payroll employee>',account_no:'123456789',earnings:{Basic:1000},deductions:{Manual:100},gross_salary:1000,total_deductions:100,net_salary:900,deduction_basis:'Manual reviewed amounts',warnings:[]}]};
        if(url==='/api/storage/native/inspect')data={storage_key:'native:100124',record_reader_version:5,folder:'/tmp/Companies/100124',format:'Tally binary .1800',name_hint:{value:'Native <&> company'},files:[{name:'Company.1800',bytes:512}],message:'Metadata preview only; connect to TallyPrime for books.'};
        if(url==='/api/storage/native/inspect' && !nativeReaderAvailable)delete data.record_reader_version;
        if(url==='/api/storage/native/read') {
            const body=JSON.parse(options.body);
            data={source:'Manager.1800',rows:[{sequence:10,page_number:1,names:['Native <unsafe> master'],fields:[{field_id:2,value:'Native <unsafe> master',byte_offset:584}],declared_page_count:1,source_pages:[1],object_type:11,scalar_candidates:[{field_id:2,type_code:9,signed_integer_raw:'9223372036854775807',unsigned_integer_raw:'9223372036854775807',payload_hex:'ffffffffffffff7f',byte_spans:[{byte_offset:620,bytes:8}]}]}],total_candidates:1,pages_checked:2,rejected_string_candidates:0,has_more:false,note:'Experimental physical records; amounts unavailable.',source_sha256:'fixture-hash'};
            if(body.kind==='vouchers')data.rows=[{sequence:15,page_number:1,search_keys:[{number:'V-1',type_name:'Custom voucher',year_token:'2026'}]}];
            if(body.kind==='transactions'){data.source='TranMgr.1800';data.rows[0].metadata=[{reference_offset:40,page_number:2,source_pages:[2]}];}
            if(body.kind==='detail'){
                data.rows[0].typed_layout={complete:false,node_count:1,stop_offset:28,diagnostic:'Unsupported encoding',nodes:[{node_id:0,parent_id:null,field_id:2,encoding_word:0x0f00,value:'<typed field>',payload_byte_spans:[{byte_offset:584,bytes:24}]}]};
                if(body.source==='TranMgr.1800'){data.rows[0].object_type=66;data.rows[0].fields.push({field_id:10,value:'fixture-guid',byte_offset:700});}
            }
        }
        if(url==='/api/storage/native/links')data={source:'LinkMgr.1800',rows:[{sequence:600,page_number:1,source_pages:[1,4],fields:[{field_id:0x235b,value:'fixture-guid',byte_spans:[{byte_offset:1016,bytes:8},{byte_offset:2064,bytes:26}]},{field_id:9006,value:'<related field>',byte_spans:[{byte_offset:2100,bytes:30}]}]}],total_candidates:1,pages_checked:5,source_sha256:'link-fixture-hash',note:'Exact string reference only; financial meaning unverified.'};
        if(url==='/api/storage/native/directory')data={directory_verified:true,financial_status_verified:false,node_count:3,root_page:3,first_page:1,last_page:4,tree_reachable:3,sibling_reachable:3,orphan_directory_nodes:0,pages_checked:5,source_sha256:'directory-fixture-hash',note:'Directory membership does not establish active or posted status.'};
        if(url==='/api/storage/native/reference/verify')data={reference_allocations_verified:true,note:'Reference subset only; complete native books remain unverified.',files:[{filename:'Transactions.xml',counts:{voucher_references:1,voucher_identity_and_date_verified:1,voucher_allocation_multisets_verified:1,ledger_entries_verified:2},unresolved_examples:[]}]};
        if(url==='/api/storage/native/sales/preview')data={voucher:{date:'2026-04-01',voucher_type:'Sales',master_id:'41',alter_id:'42',allocations:{entries:[{ledger_name:'<native ledger>',amount_decimal:'-100.00000',signed_integer_raw:'-10000000'}],balance_decimal:'0.00000'}},note:'Reference-calibrated Sales only; no XML used by this preview.'};
        if(url==='/api/storage/native/books')data={company:'Native company',books:{coverage:'Verified export subset only.',masters:[{kind:'LEDGER',name:'<native ledger>',parent:'Native group'},{kind:'GROUP',name:'Native group',parent:null}],vouchers:[{date:'2026-04-01',reference_number:'S-1',master_id:'41',native_sequence:999,allocations:[{ledger_name:'<native ledger>',amount_decimal:'-100.00000'}]}]}};
        if(url==='/api/tally/bridge/read')data={kind:'companies',rows:[{name:'Live company',guid:'g1',books_from:'20260401'}]};
        return {ok:status===200,status,json:async()=>data};
    }}
};
context.fetch=(...args)=>context.window.fetch(...args);
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/tally.js','utf8'),context);
vm.runInContext(fs.readFileSync('static/company-operations.js','utf8'),context);
vm.runInContext(fs.readFileSync('static/tally-native.js','utf8'),context);
vm.runInContext('initTheme=()=>{};initGatewayItems=()=>{};showTallyToast=()=>{};',context);
(async()=>{
    callbacks.DOMContentLoaded();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(el('company-modal').style.display,'flex');
    assert.deepEqual(requests.map(r=>r.url),['/api/storage']);
    assert(el('company-list-container').children.some(row=>row.textContent.includes('Native <&> company')));
    assert.equal(requests[0].options.cache,'no-store');
    catalogueMode='outdated';
    await vm.runInContext('loadCompaniesList()',context);
    assert(el('company-list-container').textContent.includes('running backend is outdated'));
    assert(!el('company-list-container').textContent.includes('No supported companies'));
    catalogueMode='empty';
    await vm.runInContext('loadCompaniesList()',context);
    assert(el('company-list-container').textContent.includes('No supported companies'));
    catalogueMode='native';
    await vm.runInContext('loadCompaniesList()',context);
    vm.runInContext('closeCompanyModal()',context);
    assert.equal(el('company-modal').style.display,'flex');
    vm.runInContext("companyContext='window-a';sessionStorage.setItem('tally_session_token','token-a')",context);
    await vm.runInContext("fetch('/api/state')",context);
    assert.equal(requests.at(-1).options.headers.get('X-Company-Context'),'window-a');
    assert.equal(requests.at(-1).options.headers.get('Authorization'),'Bearer token-a');
    vm.runInContext('loadAppState=async()=>{};goBackToGateway=()=>{};',context);
    await vm.runInContext("selectStoredCompany('10000:1')",context);
    assert.equal(el('auth-modal').style.display,'flex');
    assert.equal(el('auth-modal').style.zIndex,'10000');
    el('login-username').value='owner';el('login-password').value='correct';
    await vm.runInContext('submitUserLogin()',context);
    assert.equal(context.sessionStorage.getItem('tf_company_context'),'new-window-context');
    assert.equal(context.sessionStorage.getItem('tally_session_token'),'authenticated-token');
    assert.equal(context.localStorage.getItem('tally_session_token'),null);
    assert.equal(el('login-password').value,'');
    el('gst-books-period').value='2026-04';el('gst-books-registration').value='main';
    await vm.runInContext('showGSTBookReport()',context);
    assert(requests.at(-1).url.includes('period=2026-04&registration=main'));
    assert(el('operations-status').textContent.includes('No portal filing'));
    const flattened = node => [node.textContent||'',...(node.children||[]).flatMap(flattened)];
    assert(flattened(el('gst-books-report')).includes('<unsafe party>'));
    assert(flattened(el('gst-books-report')).includes('2.50'));
    el('payroll-period').value='2026-04';
    await vm.runInContext('previewMonthlyPayroll()',context);
    assert(el('operations-status').textContent.includes('Preview ready'));
    assert(flattened(el('payroll-month-report')).some(text=>text.includes('<payroll employee>')));
    assert(flattened(el('payroll-month-report')).some(text=>text.includes('••••6789')));
    el('payroll-period').value='2026-05';el('payroll-posting-date').value='2026-05-31';
    const before=requests.length;await vm.runInContext('postMonthlyPayroll()',context);
    assert.equal(requests.length,before);
    assert(el('operations-status').textContent.includes('Preview the selected month'));
    await vm.runInContext("inspectNativeCompany('native:100124')",context);
    assert.equal(el('native-tally-modal').style.display,'flex');
    assert(flattened(el('native-tally-inspection')).includes('Native <&> company'));
    assert(el('native-tally-status').textContent.includes('Metadata preview only'));
    assert.equal(el('native-record-controls').style.display,'block');
    await vm.runInContext("readNativeRecords('masters')",context);
    assert(flattened(el('native-record-results')).includes('Native <unsafe> master'));
    assert(el('native-record-status').textContent.includes('amounts unavailable'));
    assert.equal(JSON.parse(requests.at(-1).options.body).storage_key,'native:100124');
    await vm.runInContext("readNativeRecords('vouchers')",context);
    assert(flattened(el('native-record-results')).some(text=>text.includes('V-1 · Custom voucher')));
    await vm.runInContext("readNativeRecordDetail('Manager.1800',10)",context);
    assert(flattened(el('native-record-detail')).includes('Native <unsafe> master'));
    assert(flattened(el('native-record-detail')).includes('9223372036854775807'));
    assert(flattened(el('native-record-detail')).includes('<typed field>'));
    assert(flattened(el('native-record-detail')).some(text=>text.includes('Sequential field layout: partial')));
    await vm.runInContext("readNativeRecordDetail('TranMgr.1800',15)",context);
    const relatedButton=el('native-record-detail').children.find(c=>c.textContent==='Find related LinkMgr records by exact GUID');
    assert(relatedButton);
    await relatedButton.onclick();
    assert.equal(JSON.parse(requests.at(-1).options.body).voucher_guid,'fixture-guid');
    assert(flattened(el('native-record-detail')).includes('<related field>'));
    const salesButton=el('native-record-detail').children.find(c=>c.textContent==='Read calibrated Sales preview');
    assert(salesButton);await salesButton.onclick();
    assert(flattened(el('native-record-detail')).includes('<native ledger>'));
    assert(flattened(el('native-record-detail')).includes('-100.00000'));
    assert(!flattened(el('native-record-detail')).some(text=>text.includes('index undefined')));
    await vm.runInContext("readNativeRecords('transactions')",context);
    assert(flattened(el('native-record-results')).some(text=>text.includes('Transaction record sequence 10')));
    assert(flattened(el('native-record-results')).some(text=>text.includes('Header reference @40 → page 2')));
    await vm.runInContext('readNativeDirectory()',context);
    assert(flattened(el('native-record-results')).includes('Verified'));
    assert(el('native-record-status').textContent.includes('does not establish active or posted status'));
    await vm.runInContext('verifyNativeReferences()',context);
    assert(flattened(el('native-record-results')).includes('Voucher allocation multisets verified'));
    assert(el('native-record-status').textContent.includes('complete native books remain unverified'));
    await vm.runInContext('openNativeBooks()',context);
    assert(el('native-record-status').textContent.includes('Verified export subset only'));
    const bookControls=el('native-record-results').children[0];
    const findBookButton=label=>bookControls.children.find(c=>c.textContent===label);
    const bookReport=el('native-record-results').children[1];
    assert(flattened(bookReport).some(t=>t.includes('2026-04-01 · S-1')));
    bookReport.children[1].children.find(c=>c.textContent==='Open voucher').onclick();
    assert(flattened(el('native-record-detail')).includes('-100.00000'));
    findBookButton('Ledgers').onclick();
    assert(flattened(bookReport).includes('<native ledger>'));
    assert(flattened(bookReport).includes('Unavailable'));
    bookReport.children.find(c=>c.textContent==='View Sales entries: <native ledger>').onclick();
    assert(flattened(bookReport).some(t=>t.includes('2026-04-01 · S-1')));
    const bookFrom=bookControls.children.find(c=>c.type==='date');bookFrom.value='2026-05-01';bookFrom.oninput();
    assert(flattened(bookReport).some(t=>t.includes('No matching records in this subset')));
    const beforeReset=requests.length;
    vm.runInContext('openNativeTally()',context);
    await vm.runInContext("readNativeRecords('masters')",context);
    assert.equal(requests.length,beforeReset);
    assert.equal(el('native-record-controls').style.display,'none');
    nativeReaderAvailable=false;
    await vm.runInContext("inspectNativeCompany('native:100124')",context);
    assert.equal(el('native-record-controls').style.display,'none');
    assert(el('native-tally-status').textContent.includes('Fully stop TallyForge'));
    nativeReaderAvailable=true;
    el('tally-bridge-endpoint').value='http://127.0.0.1:9000';
    await vm.runInContext('readTallyCompanies()',context);
    assert.equal(el('tally-bridge-company').children[0].textContent,'Live company');
    console.log('Company UI contracts passed: startup, window context, Authorization headers, masked login, per-window credentials, scoped GST review, masked payroll preview and stale-month posting protection.');
})().catch(err=>{console.error(err);process.exitCode=1;});

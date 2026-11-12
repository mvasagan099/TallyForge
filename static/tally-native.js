let nativeRecordKey = null, nativeReadGeneration = 0;
function nativeStatus(value) {document.getElementById('native-tally-status').textContent=value;}
function openNativeTally() {
    nativeRecordKey=null;nativeReadGeneration++;
    document.getElementById('native-record-controls').style.display='none';
    ['native-tally-inspection','native-record-results','native-record-pages','native-record-detail'].forEach(id=>document.getElementById(id).replaceChildren());
    document.getElementById('native-tally-modal').style.display='flex';
    nativeStatus('Choose a native folder for inspection, or connect to a running TallyPrime server for actual ledger and voucher data.');
}
async function inspectNativeCompany(key,openBooks=false) {
    openNativeTally();
    const generation=nativeReadGeneration;
    try {
        const result=await storageRequest('native/inspect',{storage_key:key});
        if(generation!==nativeReadGeneration)return;
        const container=document.getElementById('native-tally-inspection');container.replaceChildren();
        const name=document.createElement('h3');name.textContent=result.name_hint?.value||result.storage_key;container.appendChild(name);
        const path=document.createElement('p');path.textContent=result.folder+' · '+result.format;container.appendChild(path);
        const inventory=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Native file inventory ('+result.files.length+' files)';inventory.appendChild(summary);
        appendReportTable(inventory,'Native file inventory',['File','Bytes'],result.files.map(f=>[f.name,f.bytes.toLocaleString()]));container.appendChild(inventory);
        nativeStatus(result.message);
        if(result.format==='Tally binary .1800') {
            if(result.record_reader_version!==5){nativeStatus('The running backend needs an update for direct record reading. Fully stop TallyForge, run start_tally.command again, then reload this page.');return;}
            nativeRecordKey=key;document.getElementById('native-record-controls').style.display='block';
            document.getElementById('native-record-search').value='';
            document.getElementById('native-record-status').textContent='Choose master names, voucher search keys or transaction records to read directly from the files.';
            if(openBooks && result.reference_files?.length)await openNativeBooks();
        }
    }catch(err){nativeStatus(err.message);}
}
async function readNativeRecords(kind,offset=0,search=document.getElementById('native-record-search').value.trim()) {
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration;
    const status=document.getElementById('native-record-status');
    status.textContent='Reading and checking native file pages…';
    ['native-record-results','native-record-pages','native-record-detail'].forEach(id=>document.getElementById(id).replaceChildren());
    try {
        const result=await storageRequest('native/read',{storage_key:key,kind,offset,limit:50,search});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        const container=document.getElementById('native-record-results');
        if(!result.rows.length){const empty=document.createElement('p');empty.textContent='No complete matching record candidates were decoded. This does not establish that the company has no records.';container.appendChild(empty);}
        result.rows.forEach(row=>{
            const block=document.createElement('div');block.style.cssText='padding:10px 0;border-bottom:1px solid #ddd;';
            const label=document.createElement('p');
            if(kind==='masters')label.textContent=row.names.join(' / ');
            else if(kind==='vouchers')label.textContent=row.search_keys.map(k=>`${k.number} · ${k.type_name} · Year token ${k.year_token}`).join(' / ');
            else label.textContent=`Transaction record sequence ${row.sequence} · `+row.fields.slice(0,3).map(f=>`Field 0x${f.field_id.toString(16)}: ${f.value.slice(0,120)}`).join(' / ');
            block.appendChild(label);
            if(row.metadata){const refs=document.createElement('p');refs.textContent=row.metadata.map(m=>`Header reference @${m.reference_offset} → page ${m.page_number} (${m.source_pages.length} linked pages)`).join(' · ');block.appendChild(refs);}
            const button=document.createElement('button');button.className='btn-tally-primary';button.textContent=`Inspect sequence ${row.sequence} · page ${row.page_number}`;
            button.onclick=()=>readNativeRecordDetail(result.source,row.sequence);block.appendChild(button);container.appendChild(block);
        });
        const pages=document.getElementById('native-record-pages');
        function pageButton(label,nextOffset){const button=document.createElement('button');button.className='btn-tally-primary';button.textContent=label;button.onclick=()=>readNativeRecords(kind,nextOffset,search);pages.appendChild(button);}
        if(offset)pageButton('Previous',Math.max(0,offset-50));
        if(result.has_more)pageButton('Next',offset+50);
        status.textContent=`${result.rows.length} shown of ${result.total_candidates} matching physical-page candidates${search?' · Filter: '+search:''}. ${result.pages_checked} pages passed checksum checks; ${result.rejected_string_candidates} incomplete/invalid string candidates skipped. ${result.note}`;
    }catch(err){if(generation===nativeReadGeneration)status.textContent=err.message;}
}
async function readNativeRecordDetail(source,sequence) {
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration;
    const detail=document.getElementById('native-record-detail');detail.replaceChildren();
    const heading=document.createElement('h4');heading.textContent='Reading source pages…';detail.appendChild(heading);
    try {
        const result=await storageRequest('native/read',{storage_key:key,kind:'detail',source,sequence,limit:100});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        heading.textContent=`${source} · sequence ${sequence} · ${result.total_candidates} root records`;
        const note=document.createElement('p');note.textContent='Linked root records and raw field candidates. Same-sequence association is provisional except for the explicit header references. Integers are unscaled, not currency amounts or verified dates. Active status and financial meanings remain unverified. Source SHA-256: '+result.source_sha256;detail.appendChild(note);
        const linkContainer=document.createElement('div');
        if(source==='TranMgr.1800'){
            const salesButton=document.createElement('button');salesButton.className='btn-tally-primary';salesButton.textContent='Read calibrated Sales preview';salesButton.onclick=()=>readNativeSalesPreview(sequence,linkContainer);detail.appendChild(salesButton);
            const guids=[...new Set(result.rows.filter(r=>r.object_type===66).flatMap(r=>r.fields.filter(f=>f.field_id===10).map(f=>f.value)))];
            if(guids.length===1){const button=document.createElement('button');button.className='btn-tally-primary';button.textContent='Find related LinkMgr records by exact GUID';button.onclick=()=>readNativeLinks(guids[0],linkContainer);detail.appendChild(button);}
            else if(guids.length>1){const warning=document.createElement('p');warning.textContent='Multiple GUID candidates found; automatic related-record lookup is unavailable.';detail.appendChild(warning);}
        }
        detail.appendChild(linkContainer);
        result.rows.forEach(row=>{
            appendReportTable(detail,`Root page ${row.page_number} · ${row.declared_page_count} declared / ${row.source_pages.length} joined pages · type 0x${row.object_type.toString(16)} · checksum valid`,['Field ID','Text','Byte offset'],row.fields.map(f=>['0x'+f.field_id.toString(16),f.value,f.byte_offset]));
            if(row.scalar_candidates?.length){const section=document.createElement('details'),label=document.createElement('summary');label.textContent='Raw numeric candidates · scale and meaning unverified';section.appendChild(label);appendReportTable(section,'Raw numeric candidates',['Field ID','Type code','Signed integer (raw)','Unsigned integer (raw)','Bytes (hex)','Source byte spans'],row.scalar_candidates.map(f=>['0x'+f.field_id.toString(16),'0x'+f.type_code.toString(16),f.signed_integer_raw,f.unsigned_integer_raw,f.payload_hex,f.byte_spans.map(s=>s.byte_offset+'+'+s.bytes).join(', ')]));detail.appendChild(section);}
            if(row.typed_layout)appendNativeLayout(detail,row.typed_layout);
        });
        if(result.has_more){const note=document.createElement('p');note.textContent='Only the first 100 associated root records are shown.';detail.appendChild(note);}
    }catch(err){if(generation===nativeReadGeneration)heading.textContent=err.message;}
}
async function readNativeSalesPreview(sequence,container){
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration;container.replaceChildren();
    const status=document.createElement('p');status.textContent='Reading native Sales header and allocations…';container.appendChild(status);
    try{
        const result=await storageRequest('native/sales/preview',{storage_key:key,sequence});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        const v=result.voucher;
        status.textContent=`${v.date} · ${v.voucher_type} · Master ID ${v.master_id} · Alteration ID ${v.alter_id}. ${result.note}`;
        appendReportTable(container,'Native allocations · reference-calibrated units',['Ledger','Signed amount','Raw integer'],v.allocations.entries.map(e=>[e.ledger_name,e.amount_decimal,e.signed_integer_raw]));
        const balance=document.createElement('p');balance.textContent='Allocation sum: '+v.allocations.balance_decimal+' · Posting status and opening balances remain unverified.';container.appendChild(balance);
    }catch(err){if(generation===nativeReadGeneration)status.textContent=err.message;}
}
async function verifyNativeReferences(){
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration,status=document.getElementById('native-record-status');
    ['native-record-results','native-record-pages','native-record-detail'].forEach(id=>document.getElementById(id).replaceChildren());
    status.textContent='Reading the XML references and comparing native identities, dates and allocation values…';
    try{
        const result=await storageRequest('native/reference/verify',{storage_key:key});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        const container=document.getElementById('native-record-results');
        result.files.forEach(file=>{
            const c=file.counts;
            appendReportTable(container,file.filename,['Check','Count'],[
                ['Ledger references',c.ledger_references||0],['Ledger names verified',c.ledger_names_verified||0],['Ledger parent groups verified',c.ledger_parents_verified||0],
                ['Voucher references',c.voucher_references||0],['Voucher identity/date verified',c.voucher_identity_and_date_verified||0],['Voucher allocation multisets verified',c.voucher_allocation_multisets_verified||0],['Ledger allocation entries verified',c.ledger_entries_verified||0],['Unresolved references',c.unresolved_references||0]]);
            if(file.unresolved_examples.length)appendReportTable(container,'Unresolved reference examples',['Kind','GUID','Reason'],file.unresolved_examples.map(e=>[e.kind,e.identity,e.reason]));
        });
        status.textContent=(result.reference_allocations_verified?'The exported voucher allocation subset matches. ':'The reference subset has unresolved allocation comparisons. ')+result.note;
    }catch(err){if(generation===nativeReadGeneration)status.textContent=err.message;}
}
async function openNativeBooks(){
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration,status=document.getElementById('native-record-status');
    ['native-record-results','native-record-pages','native-record-detail'].forEach(id=>document.getElementById(id).replaceChildren());
    status.textContent='Opening company preview: checking source pages and matching exported records. This may take a minute…';
    try{
        const result=await storageRequest('native/books',{storage_key:key});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        if(!result.books)throw new Error('Restart TallyForge to enable the company books preview.');
        const books=result.books,container=document.getElementById('native-record-results');
        status.textContent=`${result.company} · Read-only · ${books.masters.length} masters · ${books.vouchers.length} Sales vouchers. ${books.coverage} Opening/closing balances and posting status are unavailable.`;
        const controls=document.createElement('div');controls.style.cssText='display:flex;gap:8px;flex-wrap:wrap;padding:8px 0;';
        const search=document.createElement('input');search.className='tally-input';search.placeholder='Search ledger, group or voucher';search.setAttribute?.('aria-label','Search company books');
        const from=document.createElement('input'),to=document.createElement('input');
        from.type=to.type='date';from.setAttribute?.('aria-label','Sales from date');to.setAttribute?.('aria-label','Sales to date');
        let mode='Sales register',offset=0;
        const report=document.createElement('div'),pages=document.getElementById('native-record-pages'),detail=document.getElementById('native-record-detail');
        function button(label,action,target=controls){const b=document.createElement('button');b.className='btn-tally-primary';b.textContent=label;b.onclick=action;target.appendChild(b);return b;}
        function showVoucher(v){
            detail.replaceChildren();const h=document.createElement('h3');h.textContent=`${v.date} · Sales · ${v.reference_number||'Master ID '+v.master_id}`;detail.appendChild(h);
            appendReportTable(detail,'Native ledger allocations',['Ledger','Signed amount'],v.allocations.map(e=>[e.ledger_name,e.amount_decimal]));
            const p=document.createElement('p');p.textContent=`Native sequence ${v.native_sequence} · Master ID ${v.master_id} · Balanced allocation set. Posting status unavailable. Voucher number label comes from the XML export.`;detail.appendChild(p);
        }
        function render(){
            if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
            report.replaceChildren();pages.replaceChildren();detail.replaceChildren();
            const needle=search.value.trim().toLocaleLowerCase();
            const inText=value=>String(value??'').toLocaleLowerCase().includes(needle);
            let rows;
            if(mode==='Sales register')rows=books.vouchers.filter(v=>(!from.value||v.date>=from.value)&&(!to.value||v.date<=to.value)&&
                (!needle||inText(v.reference_number)||inText(v.master_id)||v.allocations.some(e=>inText(e.ledger_name))));
            else rows=books.masters.filter(m=>m.kind===(mode==='Ledgers'?'LEDGER':'GROUP')&&(!needle||inText(m.name)||inText(m.parent)));
            const count=document.createElement('p');count.textContent=`${mode} · ${rows.length} matching records in the verified export subset`;report.appendChild(count);
            from.disabled=to.disabled=mode!=='Sales register';
            if(from.value&&to.value&&from.value>to.value&&mode==='Sales register'){count.textContent='From date must be on or before To date.';return;}
            const shown=rows.slice(offset,offset+50);
            if(mode==='Sales register'){
                shown.forEach(v=>{
                    const row=document.createElement('div');row.style.cssText='padding:10px 0;border-bottom:1px solid #ddd;';
                    const p=document.createElement('p');p.textContent=`${v.date} · ${v.reference_number||'Master ID '+v.master_id} · `+[...new Set(v.allocations.map(e=>e.ledger_name))].join(' / ');row.appendChild(p);
                    button('Open voucher',()=>showVoucher(v),row);report.appendChild(row);
                });
            }else{
                appendReportTable(report,mode,['Name','Parent group','Opening balance','Closing balance'],shown.map(m=>[m.name,m.parent||'Primary / unavailable','Unavailable','Unavailable']));
                if(mode==='Ledgers')shown.forEach(m=>button('View Sales entries: '+m.name,()=>{mode='Sales register';search.value=m.name;offset=0;render();},report));
            }
            if(!rows.length){const p=document.createElement('p');p.textContent='No matching records in this subset. Other company records may exist outside the supplied exports.';report.appendChild(p);}
            if(offset)button('Previous',()=>{offset=Math.max(0,offset-50);render();},pages);
            if(offset+50<rows.length)button('Next',()=>{offset+=50;render();},pages);
        }
        ['Sales register','Ledgers','Groups'].forEach(label=>button(label,()=>{mode=label;offset=0;render();}));
        controls.appendChild(search);controls.appendChild(from);controls.appendChild(to);
        [search,from,to].forEach(input=>{input.oninput=()=>{offset=0;render();};});
        container.appendChild(controls);container.appendChild(report);render();
    }catch(err){if(generation===nativeReadGeneration)status.textContent=err.message;}
}
async function readNativeLinks(guid,container,offset=0) {
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration;
    container.replaceChildren();
    const status=document.createElement('p');status.textContent='Checking LinkMgr pages and exact GUID references…';container.appendChild(status);
    try{
        const result=await storageRequest('native/links',{storage_key:key,voucher_guid:guid,offset,limit:50});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        status.textContent=`${result.rows.length} shown of ${result.total_candidates} exact matching record candidates; ${result.sequentially_verified_matches??0} confirmed by sequential field boundaries. ${result.pages_checked} pages checked. ${result.note} Source SHA-256: ${result.source_sha256}`;
        result.rows.forEach(row=>{
            const basis=document.createElement('p');basis.textContent='Match basis: '+(row.match_basis||'Literal marker candidate; layout unverified');container.appendChild(basis);
            appendReportTable(container,`LinkMgr root ${row.page_number} · independent sequence ${row.sequence} · ${row.source_pages.length} linked pages`,['Field ID','Text','Source byte spans'],row.fields.map(f=>['0x'+f.field_id.toString(16),f.value,f.byte_spans.map(s=>s.byte_offset+'+'+s.bytes).join(', ')]));
            if(row.typed_layout)appendNativeLayout(container,row.typed_layout);
        });
        function pageButton(label,next){const button=document.createElement('button');button.className='btn-tally-primary';button.textContent=label;button.onclick=()=>readNativeLinks(guid,container,next);container.appendChild(button);}
        if(offset)pageButton('Previous related records',Math.max(0,offset-50));
        if(result.has_more)pageButton('Next related records',offset+50);
    }catch(err){if(generation===nativeReadGeneration)status.textContent=err.message;}
}
function appendNativeLayout(container,layout){
    const section=document.createElement('details'),label=document.createElement('summary');
    label.textContent=`Sequential field layout: ${layout.complete?'complete':'partial'} · ${layout.node_count} fields · meanings unverified`;section.appendChild(label);
    const coverage=document.createElement('p');coverage.textContent=`Field decoding starts at logical offset ${layout.start_offset??'unknown'}. Record prefixes and financial meanings remain unverified.`;section.appendChild(coverage);
    if(!layout.complete){const warning=document.createElement('p');warning.textContent=`Stopped at logical offset ${layout.stop_offset}: ${layout.diagnostic}. Unsupported bytes are not treated as decoded fields.`;section.appendChild(warning);}
    appendReportTable(section,'Serialized field boundaries',['Node / parent','Field ID','Flags','Encoding','Value or raw bytes','Opaque references (raw)','Payload byte spans'],layout.nodes.map(n=>[n.node_id+' / '+(n.parent_id??'root'),'0x'+n.field_id.toString(16),'0x'+(n.flags??0).toString(16),'0x'+n.encoding_word.toString(16),n.value??n.signed_integer_raw??n.payload_hex??'Container',(n.references||[]).map(r=>'0x'+r.flag_bit.toString(16)+': '+r.unsigned_integer_raw).join(', '),n.payload_byte_spans.map(s=>s.byte_offset+'+'+s.bytes).join(', ')]));container.appendChild(section);
}
async function readNativeDirectory(){
    if(!nativeRecordKey)return;
    const key=nativeRecordKey,generation=++nativeReadGeneration,status=document.getElementById('native-record-status');
    ['native-record-results','native-record-pages','native-record-detail'].forEach(id=>document.getElementById(id).replaceChildren());
    status.textContent='Checking all transaction pages, directory tree and reciprocal sibling links…';
    try{
        const result=await storageRequest('native/directory',{storage_key:key});
        if(generation!==nativeReadGeneration || key!==nativeRecordKey)return;
        appendReportTable(document.getElementById('native-record-results'),'Physical transaction directory · financial status unverified',['Check','Result'],[
            ['Structurally coherent directory',result.directory_verified?'Verified':'Not verified'],
            ['Directory nodes',result.node_count],['Tree root page',result.root_page],['First / last sibling pages',result.first_page+' / '+result.last_page],
            ['Tree-reachable nodes',result.tree_reachable],['Sibling-reachable nodes',result.sibling_reachable],['Orphan directory nodes',result.orphan_directory_nodes],['Pages checksum-checked',result.pages_checked]]);
        status.textContent=result.note+' Source SHA-256: '+result.source_sha256;
    }catch(err){if(generation===nativeReadGeneration)status.textContent=err.message;}
}
function bridgeArguments(kind,offset=0) {
    return {endpoint:document.getElementById('tally-bridge-endpoint').value.trim(),kind,company:kind==='companies'?'':document.getElementById('tally-bridge-company').value,
            from_date:kind==='companies'?'':document.getElementById('tally-bridge-from').value,to_date:kind==='companies'?'':document.getElementById('tally-bridge-to').value,limit:500,offset};
}
async function readTallyCompanies() {
    nativeStatus('Reading company list from TallyPrime…');
    try {
        const result=await companyOperation('/api/tally/bridge/read',bridgeArguments('companies'));
        operationOptions('tally-bridge-company',result.rows.map(r=>[r.name,r.name]));
        const container=document.getElementById('tally-bridge-results');container.replaceChildren();
        appendReportTable(container,'Companies returned by TallyPrime',['Company','GUID','Books from'],result.rows.map(r=>[r.name,r.guid,r.books_from]));
        document.getElementById('tally-bridge-pages').replaceChildren();
        nativeStatus(result.rows.length+' companies returned by TallyPrime. Select one to read its books.');
    }catch(err){nativeStatus(err.message);}
}
async function readTallyBooks(kind,offset=0) {
    nativeStatus('Reading '+kind+' from TallyPrime…');
    try {
        const result=await companyOperation('/api/tally/bridge/read',bridgeArguments(kind,offset));
        const container=document.getElementById('tally-bridge-results');container.replaceChildren();
        if(kind==='ledgers')appendReportTable(container,result.company+' · Ledgers',['Ledger','Group','Opening (Tally)','Closing (Tally)'],result.rows.map(r=>[r.name,r.parent,r.opening_balance_raw,r.closing_balance_raw]));
        else {
            appendReportTable(container,result.company+' · Vouchers',['Date (Tally)','Number','Type','Party','Amount (Tally)','Cancelled'],result.rows.map(r=>[r.date_raw,r.number,r.voucher_type,r.party,r.amount_raw||'Not returned',r.is_cancelled]));
            result.rows.filter(r=>r.entries.length).forEach(v=>{
                const details=document.createElement('details'),label=document.createElement('summary');label.textContent=v.number+' · Ledger entries';details.appendChild(label);
                appendReportTable(details,'Amounts as returned by Tally',['Ledger','Amount (Tally)'],v.entries.map(e=>[e.ledger,e.amount_raw]));container.appendChild(details);
            });
        }
        const pages=document.getElementById('tally-bridge-pages');pages.replaceChildren();
        if(offset>0){const prev=document.createElement('button');prev.textContent='Previous';prev.onclick=()=>readTallyBooks(kind,Math.max(0,offset-500));pages.appendChild(prev);}
        if(result.has_more){const next=document.createElement('button');next.textContent='Next';next.onclick=()=>readTallyBooks(kind,offset+500);pages.appendChild(next);}
        nativeStatus(`${result.returned} ${kind} shown from this response (${result.total_in_response} returned by Tally). ${result.note}`);
    }catch(err){nativeStatus(err.message);}
}

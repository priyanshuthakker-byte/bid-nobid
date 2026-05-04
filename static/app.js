/* ═══════════════════════════════════════════════════════
   Nascent Bid Intelligence — App JS v2
   All API calls preserved, new clean architecture
═══════════════════════════════════════════════════════ */

'use strict';

/* ── State ── */
var S = {
  tenders: [],
  dashTenders: [],
  dashFilter: 'all',
  dashSort: 'deadline',
  dashPage: 1,
  dashPerPage: 20,
  dashSearch: '',
  tlFilter: '',
  tlSort: 'deadline',
  tlPage: 1,
  tlPerPage: 25,
  tlSearch: '',
  analysis: null,
  analysisTid: '',
  analysisFilename: '',
  anTab: 'pq',
  pbrStore: {},
  boqItems: [],
  calYear: new Date().getFullYear(),
  calMonth: new Date().getMonth(),
  stageTid: '',
  stageModal: {},
  currentPage: 'dashboard',
};

var STAGES = ['Identified','Analysed','Approval Pending','Pre-bid Sent',
  'Pre-bid Closed','Documents Ready','Submitted','L1 Awaited','Won','Lost','No-Bid'];
var STAGE_COLORS = {
  'Identified':'#6366f1','Analysed':'#06b6d4','Approval Pending':'#f59e0b',
  'Pre-bid Sent':'#8b5cf6','Pre-bid Closed':'#a855f7','Documents Ready':'#22c55e',
  'Submitted':'#3b82f6','L1 Awaited':'#f97316','Won':'#22c55e','Lost':'#ef4444','No-Bid':'#475569'
};

/* ════════════════════════════════════════════════════════
   NAVIGATION
════════════════════════════════════════════════════════ */
function showPage(name) {
  document.querySelectorAll('.page').forEach(function(p){ p.classList.remove('active'); });
  document.querySelectorAll('.nav-item').forEach(function(n){ n.classList.remove('active'); });
  var pg = document.getElementById('page-' + name);
  var nav = document.getElementById('nav-' + name);
  if (pg) pg.classList.add('active');
  if (nav) nav.classList.add('active');
  S.currentPage = name;

  if (name === 'dashboard') loadDashboard();
  else if (name === 'tenders') { if (!S.tenders.length) loadTenders(); else renderTenders(); }
  else if (name === 'pipeline') loadPipeline();
  else if (name === 'calendar') renderCalendar();
  else if (name === 'settings') loadSettings();
}

/* ════════════════════════════════════════════════════════
   UTILITIES
════════════════════════════════════════════════════════ */
function toast(msg, type) {
  type = type || 'inf';
  var map = {success:'ok', error:'err', info:'inf', warning:'warn', ok:'ok', err:'err', inf:'inf', warn:'warn'};
  var cls = map[type] || 'inf';
  var el = document.createElement('div');
  el.className = 'toast ' + cls;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(function(){ el.style.animation = 'toastIn .3s ease reverse'; setTimeout(function(){ el.remove(); }, 300); }, 3500);
}

function showLoading(msg) {
  document.getElementById('loading').classList.add('on');
  document.getElementById('loading-txt').textContent = msg || 'Loading…';
}
function hideLoading() { document.getElementById('loading').classList.remove('on'); }

function fmtCr(val) {
  if (!val && val !== 0) return '—';
  var n = parseFloat(val);
  if (isNaN(n)) return '—';
  if (n >= 1e9) return (n/1e7).toFixed(1) + ' Cr';
  if (n >= 1e7) return (n/1e7).toFixed(2) + ' Cr';
  if (n >= 1e5) return (n/1e5).toFixed(1) + ' L';
  return '₹' + n.toLocaleString('en-IN');
}

function fmtDate(d) {
  if (!d) return '—';
  var dt = new Date(d);
  if (isNaN(dt)) return d;
  return dt.toLocaleDateString('en-IN', {day:'2-digit', month:'short', year:'numeric'});
}

function daysLeft(d) {
  if (!d) return null;
  var now = new Date(); now.setHours(0,0,0,0);
  var dt = new Date(d); dt.setHours(0,0,0,0);
  return Math.round((dt - now) / 86400000);
}

function dlChip(deadline) {
  var days = daysLeft(deadline);
  if (days === null) return '<span class="t-muted">—</span>';
  var cls = days < 0 ? 'dl-hot' : days <= 3 ? 'dl-hot' : days <= 7 ? 'dl-soon' : 'dl-ok';
  var txt = days < 0 ? 'Expired' : days === 0 ? 'Today!' : days === 1 ? 'Tomorrow' : days + 'd left';
  return '<span class="dl ' + cls + '">' + txt + '</span>';
}

function verdictBadge(v) {
  if (!v) return '<span class="badge badge-gray">—</span>';
  return '<span class="badge v-' + v + '">' + v + '</span>';
}

function stageBadge(s) {
  if (!s) return '';
  var c = STAGE_COLORS[s] || '#475569';
  return '<span style="background:' + c + '22;color:' + c + ';border:1px solid ' + c + '44;padding:2px 8px;border-radius:99px;font-size:10.5px;font-weight:600">' + s + '</span>';
}

function countUp(elId, target, duration) {
  var el = document.getElementById(elId);
  if (!el) return;
  var start = 0, dur = duration || 600, step = 16;
  var steps = dur / step;
  var inc = target / steps;
  var cur = 0;
  var timer = setInterval(function() {
    cur = Math.min(cur + inc, target);
    el.textContent = Math.round(cur).toLocaleString('en-IN');
    if (cur >= target) clearInterval(timer);
  }, step);
}

function closeModal(id) {
  var el = document.getElementById(id);
  if (el) el.classList.remove('on');
}

function openModal(id) {
  var el = document.getElementById(id);
  if (el) el.classList.add('on');
}

/* ════════════════════════════════════════════════════════
   DASHBOARD
════════════════════════════════════════════════════════ */
function loadDashboard() {
  var dateEl = document.getElementById('dash-date');
  if (dateEl) dateEl.textContent = new Date().toLocaleDateString('en-IN', {weekday:'long', day:'numeric', month:'long', year:'numeric'});

  fetch('/dashboard').then(function(r){ return r.json(); }).then(function(data) {
    var tenders = data.tenders || data || [];
    S.dashTenders = tenders;
    if (!S.tenders.length) S.tenders = tenders;

    // Stats
    var total = tenders.length;
    var now = new Date(); now.setHours(0,0,0,0);
    var urgent = tenders.filter(function(t){
      var days = daysLeft(t.bid_submission_date || t.deadline);
      return days !== null && days >= 0 && days <= 7;
    }).length;
    var bid = tenders.filter(function(t){ return (t.verdict||'').toUpperCase() === 'BID'; }).length;
    var active = tenders.filter(function(t){
      var s = t.stage||'';
      return ['Approval Pending','Pre-bid Sent','Pre-bid Closed','Documents Ready','Submitted','L1 Awaited'].includes(s);
    }).length;

    countUp('s-total', total);
    countUp('s-urgent', urgent);
    countUp('s-bid', bid);
    countUp('s-active', active);

    // Populate state filter
    var states = {};
    tenders.forEach(function(t){ var s = t.state||t.location||''; if(s) states[s]=1; });
    var sel = document.getElementById('dash-state');
    if (sel && sel.options.length <= 1) {
      Object.keys(states).sort().forEach(function(s){
        var o = document.createElement('option'); o.value=s; o.textContent=s; sel.appendChild(o);
      });
    }

    // Alerts
    var alertsEl = document.getElementById('dash-alerts');
    var urgent3 = tenders.filter(function(t){ var d = daysLeft(t.bid_submission_date||t.deadline); return d !== null && d >= 0 && d <= 3; });
    if (alertsEl) {
      if (urgent3.length === 0) {
        alertsEl.innerHTML = '<div style="color:var(--text3);font-size:12px">✓ No urgent deadlines</div>';
      } else {
        alertsEl.innerHTML = urgent3.slice(0,5).map(function(t){
          var d = daysLeft(t.bid_submission_date||t.deadline);
          return '<div style="display:flex;align-items:center;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px">' +
            '<span style="color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:8px">' + (t.tender_name||t.brief||'Unnamed').substring(0,40) + '</span>' +
            dlChip(t.bid_submission_date||t.deadline) + '</div>';
        }).join('');
      }
    }

    // Pipeline mini
    var pipelineEl = document.getElementById('dash-pipeline');
    if (pipelineEl) {
      var counts = {};
      tenders.forEach(function(t){ var s=t.stage||'Identified'; counts[s]=(counts[s]||0)+1; });
      pipelineEl.innerHTML = Object.keys(counts).map(function(s){
        var c = STAGE_COLORS[s]||'#6366f1';
        var pct = Math.min(100, Math.round((counts[s]/total)*100*5));
        return '<div style="display:flex;align-items:center;gap:8px;font-size:12px">' +
          '<span style="flex:0 0 110px;color:var(--text2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + s + '</span>' +
          '<div style="flex:1;height:4px;background:var(--glass2);border-radius:99px"><div style="width:' + pct + '%;height:100%;background:' + c + ';border-radius:99px"></div></div>' +
          '<span style="color:' + c + ';font-weight:600;min-width:20px;text-align:right">' + counts[s] + '</span>' +
          '</div>';
      }).join('');
    }

    renderDash();
  }).catch(function(e){ toast('Dashboard load failed: ' + e.message, 'error'); });

  // Win/loss
  fetch('/win-loss').then(function(r){ return r.json(); }).then(function(d){
    var won = d.won||0, lost = d.lost||0;
    var el = document.getElementById('wl-won'); if(el) el.textContent = won;
    el = document.getElementById('wl-lost'); if(el) el.textContent = lost;
    el = document.getElementById('wl-rate');
    if(el) el.textContent = (won+lost>0) ? Math.round(won/(won+lost)*100)+'%' : '—';
  }).catch(function(){});
}

function setDashFilter(f) {
  S.dashFilter = f;
  S.dashPage = 1;
  ['all','bid','review','urgent'].forEach(function(x){
    var btn = document.getElementById('df-' + x);
    if (btn) btn.classList.toggle('on', x === f);
  });
  renderDash();
}

function setDashSort(col) {
  S.dashSort = S.dashSort === col ? col + '_desc' : col;
  renderDash();
}

function renderDash() {
  var search = (document.getElementById('dash-search')||{}).value||'';
  var state = (document.getElementById('dash-state')||{}).value||'';
  var source = (document.getElementById('dash-source')||{}).value||'';
  var f = S.dashFilter;

  var rows = S.dashTenders.filter(function(t) {
    var v = (t.verdict||'').toUpperCase();
    var days = daysLeft(t.bid_submission_date||t.deadline);
    if (f === 'bid' && v !== 'BID') return false;
    if (f === 'review' && v !== 'REVIEW') return false;
    if (f === 'urgent' && !(days !== null && days >= 0 && days <= 7)) return false;
    if (search) {
      var hay = ((t.tender_name||t.brief||'') + ' ' + (t.org_name||t.organization||'')).toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    if (state && (t.state||t.location||'') !== state) return false;
    if (source) {
      var isgem = (t.source||t.tender_type||'').toLowerCase().includes('gem');
      if (source === 'gem' && !isgem) return false;
      if (source === 'nongem' && isgem) return false;
    }
    return true;
  });

  // Sort
  rows.sort(function(a,b){
    var col = S.dashSort.replace('_desc','');
    var desc = S.dashSort.endsWith('_desc');
    var va, vb;
    if (col === 'deadline') {
      va = new Date(a.bid_submission_date||a.deadline||0).getTime();
      vb = new Date(b.bid_submission_date||b.deadline||0).getTime();
    } else if (col === 'cost') {
      va = parseFloat(a.estimated_cost||0);
      vb = parseFloat(b.estimated_cost||0);
    } else if (col === 'org') {
      va = (a.org_name||a.organization||'').toLowerCase();
      vb = (b.org_name||b.organization||'').toLowerCase();
    } else {
      va = (a.tender_name||a.brief||'').toLowerCase();
      vb = (b.tender_name||b.brief||'').toLowerCase();
    }
    var r = va < vb ? -1 : va > vb ? 1 : 0;
    return desc ? -r : r;
  });

  // Paginate
  var perPage = S.dashPerPage;
  var total = rows.length;
  var pages = Math.max(1, Math.ceil(total / perPage));
  S.dashPage = Math.min(S.dashPage, pages);
  var slice = rows.slice((S.dashPage-1)*perPage, S.dashPage*perPage);

  var tbody = document.getElementById('dash-tbody');
  if (!tbody) return;
  if (!slice.length) {
    tbody.innerHTML = '<tr><td colspan="7"><div class="empty"><div class="empty-ico">🔍</div><div class="empty-title">No tenders match filter</div></div></td></tr>';
  } else {
    tbody.innerHTML = slice.map(function(t) {
      var name = (t.tender_name||t.brief||'Unnamed').substring(0,65);
      var org = (t.org_name||t.organization||'—').substring(0,30);
      var cost = fmtCr(t.estimated_cost);
      var dl = t.bid_submission_date||t.deadline;
      var tid = t.t247_id||t.id||'';
      return '<tr onclick="openTenderModal(\'' + tid + '\')">' +
        '<td><div class="trunc" style="max-width:240px;font-weight:500;color:var(--text)">' + name + '</div></td>' +
        '<td style="color:var(--text2);font-size:12px">' + org + '</td>' +
        '<td style="font-family:var(--mono);font-size:12px;color:var(--cyan)">' + cost + '</td>' +
        '<td>' + dlChip(dl) + '</td>' +
        '<td>' + verdictBadge(t.verdict) + '</td>' +
        '<td>' + stageBadge(t.stage) + '</td>' +
        '<td style="white-space:nowrap">' +
          '<button class="btn btn-ghost btn-xs" onclick="event.stopPropagation();openStageModal(\'' + tid + '\',\'' + (t.tender_name||t.brief||'').replace(/'/g,''').substring(0,40) + '\')" title="Update Stage">📊</button> ' +
          '<button class="btn btn-primary btn-xs" onclick="event.stopPropagation();loadTenderAnalysis(\'' + tid + '\')" title="Analyse">🔍</button>' +
        '</td></tr>';
    }).join('');
  }

  // Pager
  renderPager('dash-pager', pages, S.dashPage, function(p){ S.dashPage=p; renderDash(); });
}

/* ════════════════════════════════════════════════════════
   ALL TENDERS
════════════════════════════════════════════════════════ */
function loadTenders() {
  showLoading('Loading all tenders…');
  fetch('/tenders').then(function(r){ return r.json(); }).then(function(data){
    S.tenders = Array.isArray(data) ? data : (data.tenders||[]);
    hideLoading();
    // Populate state filter
    var states = {};
    S.tenders.forEach(function(t){ var s=t.state||t.location||''; if(s) states[s]=1; });
    var sel = document.getElementById('tl-state');
    if (sel) {
      sel.innerHTML = '<option value="">All States</option>';
      Object.keys(states).sort().forEach(function(s){ var o=document.createElement('option');o.value=s;o.textContent=s;sel.appendChild(o); });
    }
    renderTenders();
  }).catch(function(e){ hideLoading(); toast('Failed to load tenders: '+e.message,'error'); });
}

function setTlFilter(f) {
  S.tlFilter = f;
  S.tlPage = 1;
  document.querySelectorAll('[id^="tl-f-"]').forEach(function(b){ b.classList.remove('on'); });
  var id = 'tl-f-' + f.toLowerCase().replace(' ','').replace('-','') || 'tl-f-all';
  var btn = document.getElementById(id);
  if (!btn) btn = document.getElementById('tl-f-all');
  if (btn) btn.classList.add('on');
  renderTenders();
}

function renderTenders() {
  var search = (document.getElementById('tl-search')||{}).value||'';
  var state  = (document.getElementById('tl-state')||{}).value||'';
  var sortV  = (document.getElementById('tl-sort')||{}).value||'deadline';
  var perPage = parseInt((document.getElementById('tl-per-page')||{}).value||25);
  var f = S.tlFilter;

  var rows = S.tenders.filter(function(t) {
    var v = (t.verdict||'').toUpperCase();
    var src = (t.source||t.tender_type||'').toLowerCase();
    var days = daysLeft(t.bid_submission_date||t.deadline);
    if (f === 'BID' && v !== 'BID') return false;
    if (f === 'REVIEW' && v !== 'REVIEW') return false;
    if (f === 'NO-BID' && v !== 'NO-BID') return false;
    if (f === 'unanalysed' && v) return false;
    if (f === 'gem' && !src.includes('gem')) return false;
    if (f === 'msme' && !(t.msme_exempt||t.msme_exemption||'').toString().toLowerCase().includes('yes')) return false;
    if (search) {
      var hay = [(t.tender_name||t.brief||''),(t.org_name||t.organization||''),(t.ref_no||''),(t.location||t.state||'')].join(' ').toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    if (state && (t.state||t.location||'') !== state) return false;
    return true;
  });

  // Sort
  rows.sort(function(a,b){
    if (sortV==='deadline') {
      var da=new Date(a.bid_submission_date||a.deadline||0).getTime();
      var db=new Date(b.bid_submission_date||b.deadline||0).getTime();
      return da-db;
    } else if (sortV==='cost_desc') return parseFloat(b.estimated_cost||0)-parseFloat(a.estimated_cost||0);
    else if (sortV==='cost_asc')  return parseFloat(a.estimated_cost||0)-parseFloat(b.estimated_cost||0);
    else if (sortV==='org') return (a.org_name||a.organization||'').localeCompare(b.org_name||b.organization||'');
    return 0;
  });

  S.tlPerPage = perPage;
  var total = rows.length;
  var pages = Math.max(1, Math.ceil(total/perPage));
  S.tlPage = Math.min(S.tlPage, pages);
  var slice = rows.slice((S.tlPage-1)*perPage, S.tlPage*perPage);

  var lbl = document.getElementById('tl-count-lbl');
  if (lbl) lbl.textContent = total.toLocaleString('en-IN') + ' tenders' + (f ? ' · filtered' : '');

  var tbody = document.getElementById('tl-tbody');
  if (!tbody) return;
  if (!slice.length) {
    tbody.innerHTML = '<tr><td colspan="11"><div class="empty"><div class="empty-ico">📭</div><div class="empty-title">No tenders match</div></div></td></tr>';
  } else {
    tbody.innerHTML = slice.map(function(t){
      var tid = t.t247_id||t.id||'';
      var brief = (t.tender_name||t.brief||'Unnamed').substring(0,60);
      var org = (t.org_name||t.organization||'—').substring(0,28);
      var loc = (t.state||t.location||'—').substring(0,18);
      var dl = t.bid_submission_date||t.deadline;
      var msme = (t.msme_exempt||t.msme_exemption||'');
      var isMsme = msme.toString().toLowerCase().includes('yes');
      return '<tr onclick="openTenderModal(\'' + tid + '\')">' +
        '<td style="font-family:var(--mono);font-size:11px;color:var(--text3)">' + (tid||'—') + '</td>' +
        '<td style="max-width:260px"><div class="trunc fw6" style="color:var(--text)">' + brief + '</div></td>' +
        '<td style="font-size:12px;color:var(--text2)">' + org + '</td>' +
        '<td style="font-size:11px;color:var(--text3)">' + loc + '</td>' +
        '<td style="font-family:var(--mono);color:var(--cyan)">' + fmtCr(t.estimated_cost) + '</td>' +
        '<td style="font-family:var(--mono);font-size:11px;color:var(--text3)">' + fmtCr(t.emd) + '</td>' +
        '<td style="text-align:center">' + (isMsme?'<span style="color:var(--green)">✓</span>':'') + '</td>' +
        '<td>' + dlChip(dl) + '</td>' +
        '<td>' + verdictBadge(t.verdict) + '</td>' +
        '<td>' + stageBadge(t.stage) + '</td>' +
        '<td style="white-space:nowrap">' +
          '<button class="btn btn-ghost btn-xs" onclick="event.stopPropagation();openStageModal(\'' + tid + '\',\'' + brief.replace(/'/g,''') + '\')" title="Stage">📊</button> ' +
          '<button class="btn btn-primary btn-xs" onclick="event.stopPropagation();loadTenderAnalysis(\'' + tid + '\')" title="Analyse">🔍</button>' +
        '</td></tr>';
    }).join('');
  }

  renderPager('tl-pager', pages, S.tlPage, function(p){ S.tlPage=p; renderTenders(); });
}

/* ════════════════════════════════════════════════════════
   ANALYSE
════════════════════════════════════════════════════════ */
var _lastJobId = '';

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('an-drop').classList.remove('over');
  var files = e.dataTransfer.files;
  var input = document.getElementById('an-files');
  input.files = files;
  updateFileList(input);
}

function updateFileList(input) {
  var el = document.getElementById('an-filelist');
  if (!el) return;
  var names = Array.from(input.files).map(function(f){ return '📄 ' + f.name + ' (' + (f.size/1024).toFixed(0) + ' KB)'; });
  el.innerHTML = names.join('<br>');
}

async function analyseTender() {
  var files = document.getElementById('an-files').files;
  var tid = document.getElementById('an-tid').value.trim();
  if (!files.length && !tid) { toast('Upload files or enter T247 ID', 'warning'); return; }

  var fd = new FormData();
  Array.from(files).forEach(function(f){ fd.append('files', f); });
  if (tid) fd.append('t247_id', tid);

  showAnalysisProgress();
  try {
    var r = await fetch('/analyse-async', {method:'POST', body:fd});
    var d = await r.json();
    if (d.job_id) { _lastJobId = d.job_id; pollJob(d.job_id); }
    else if (d.verdict || d.tender_data) showAnalysisResult(d);
    else toast(d.detail||d.message||'Analysis failed', 'error');
  } catch(e) { hideAnalysisProgress(); toast('Error: '+e.message, 'error'); }
}

async function analyseTenderNoAI() {
  var files = document.getElementById('an-files').files;
  if (!files.length) { toast('Upload files first', 'warning'); return; }
  var fd = new FormData();
  Array.from(files).forEach(function(f){ fd.append('files',f); });
  showAnalysisProgress();
  try {
    var r = await fetch('/analyse', {method:'POST', body:fd});
    var d = await r.json();
    hideAnalysisProgress();
    if (d.verdict||d.tender_data) showAnalysisResult(d);
    else toast(d.detail||'Analysis returned no result', 'error');
  } catch(e) { hideAnalysisProgress(); toast('Error: '+e.message,'error'); }
}

async function fetchFromT247() {
  var tid = document.getElementById('an-tid').value.trim();
  if (!tid) { toast('Enter T247 ID first', 'warning'); return; }
  showLoading('Fetching from T247…');
  try {
    var r = await fetch('/tender/' + tid + '/fetch', {method:'POST'});
    var d = await r.json();
    hideLoading();
    toast(d.message||'Fetched', 'success');
  } catch(e) { hideLoading(); toast('T247 fetch: '+e.message,'error'); }
}

async function reanalyseSaved() {
  var tid = document.getElementById('an-tid').value.trim() || S.analysisTid;
  if (!tid) { toast('Enter T247 ID', 'warning'); return; }
  showLoading('Loading saved analysis…');
  try {
    var r = await fetch('/tender/' + tid + '/reanalyse', {method:'POST'});
    var d = await r.json();
    hideLoading();
    if (d.tender_data||d.verdict) showAnalysisResult(d);
    else toast(d.detail||d.message||'No saved data — analyse fresh first', 'error');
  } catch(e) { hideLoading(); toast('Error: '+e.message,'error'); }
}

async function loadTenderAnalysis(tid) {
  showPage('analyse');
  document.getElementById('an-tid').value = tid;
  showLoading('Loading analysis for '+tid+'…');
  try {
    var r = await fetch('/tender/'+tid+'/reanalyse',{method:'POST'});
    var d = await r.json();
    hideLoading();
    if (d.tender_data||d.verdict) showAnalysisResult(d);
    else toast('No analysis yet — upload documents to analyse','info');
  } catch(e) { hideLoading(); toast('Error: '+e.message,'error'); }
}

var _apTimer;
function showAnalysisProgress() {
  document.getElementById('an-upload-card').style.display='none';
  document.getElementById('an-progress').style.display='block';
  document.getElementById('an-result').style.display='none';
  var steps = ['Extracting text from documents','Parsing tender metadata','Running AI eligibility analysis','Generating PQ criteria','Building checklist & queries'];
  document.getElementById('ap-steps').innerHTML = steps.map(function(s,i){
    return '<div class="step" id="apstep-'+i+'"><div class="step-dot"></div><span>'+s+'</span></div>';
  }).join('');
  var elapsed = 0;
  _apTimer = setInterval(function(){
    elapsed++;
    var el = document.getElementById('ap-elapsed');
    if (el) el.textContent = elapsed + 's';
    var pct = Math.min(90, elapsed*2);
    var bar = document.getElementById('ap-bar');
    if (bar) bar.style.width = pct + '%';
    var stepIdx = Math.min(4, Math.floor(elapsed/6));
    for (var i=0;i<5;i++) {
      var s = document.getElementById('apstep-'+i);
      if (!s) continue;
      s.className = 'step' + (i<stepIdx?' done':i===stepIdx?' cur':'');
    }
  }, 1000);
}

function hideAnalysisProgress() {
  clearInterval(_apTimer);
  document.getElementById('an-progress').style.display='none';
  document.getElementById('an-upload-card').style.display='block';
}

var _pollTimer;
function pollJob(jobId) {
  clearInterval(_pollTimer);
  _pollTimer = setInterval(async function(){
    try {
      var r = await fetch('/job/' + jobId);
      var d = await r.json();
      if (d.status === 'done') {
        clearInterval(_pollTimer);
        document.getElementById('ap-bar').style.width='100%';
        setTimeout(function(){
          hideAnalysisProgress();
          var result = d.result || d;
          showAnalysisResult(result);
        }, 600);
      } else if (d.status === 'error') {
        clearInterval(_pollTimer);
        hideAnalysisProgress();
        toast('Analysis error: '+(d.error||'Unknown'), 'error');
      }
    } catch(e) { clearInterval(_pollTimer); hideAnalysisProgress(); toast('Poll error: '+e.message,'error'); }
  }, 2000);
}

function showAnalysisResult(d) {
  S.analysis = d;
  var td = d.tender_data || d;
  S.analysisTid = d.t247_id||(td.t247_id)||'';
  S.analysisFilename = d.download_file||d.doc_filename||'';

  hideAnalysisProgress();
  document.getElementById('an-upload-card').style.display='none';
  document.getElementById('an-result').style.display='block';

  var v = ((td.overall_verdict||{}).verdict||td.verdict||'REVIEW').toUpperCase();
  var conf = (td.overall_verdict||{}).confidence||td.confidence||'';
  var reason = (td.overall_verdict||{}).reason||td.reason||'';

  // Verdict bar
  var vbar = document.getElementById('res-verdict');
  vbar.className = 'verdict-wrap ' + v;
  document.getElementById('res-stamp').textContent = v;
  document.getElementById('res-conf').textContent = conf ? 'Confidence: '+conf : '';
  document.getElementById('res-reason').textContent = reason;

  // AI badge
  var badge = document.getElementById('res-ai-badge');
  if (d.ai_used) badge.innerHTML = '<span class="badge badge-green">✓ AI Analysed</span>';
  else badge.innerHTML = '<span class="badge badge-amber">⚙ Rule-Based</span>';

  // KPIs
  var kpis = [
    [td.estimated_cost ? fmtCr(td.estimated_cost) : null, 'Est. Cost'],
    [td.emd ? fmtCr(td.emd) : null, 'EMD'],
    [td.bid_submission_date||td.deadline ? fmtDate(td.bid_submission_date||td.deadline) : null, 'Deadline'],
    [td.prebid_query_date ? fmtDate(td.prebid_query_date) : null, 'Pre-bid Date'],
    [td.has_corrigendum ? '⚠ Yes' : null, 'Corrigendum'],
  ].filter(function(x){ return x[0]; });
  document.getElementById('res-kpis').innerHTML = kpis.map(function(k){
    return '<div class="kpi"><div class="kpi-v">'+k[0]+'</div><div class="kpi-l">'+k[1]+'</div></div>';
  }).join('');

  // PQ tab
  var pq = td.pq_criteria||[];
  document.getElementById('tc-pq').textContent = pq.length;
  var pqMet = pq.filter(function(c){ return (c.nascent_status||c.status||'').toUpperCase()==='MET'; }).length;
  document.getElementById('res-pq-sum').innerHTML = pq.length ?
    '<span class="t-green">✓ '+pqMet+' Met</span><span style="margin:0 8px;color:var(--text3)">·</span><span class="t-red">✗ '+(pq.length-pqMet)+' Issues</span>' : '';
  document.querySelector('#res-pq-tbl tbody').innerHTML = pq.map(function(c,i){
    var st = (c.nascent_status||c.status||'REVIEW').toUpperCase().replace(/ /g,'-');
    return '<tr><td style="color:var(--text3)">'+(i+1)+'</td>' +
      '<td style="color:var(--text);font-size:12.5px">'+(c.criterion||c.criteria||c.name||'')+'</td>' +
      '<td style="font-size:11px;color:var(--text2)">'+(c.required_value||c.requirement||c.value||'—')+'</td>' +
      '<td><span class="pill pill-'+st+'">'+st+'</span></td>' +
      '<td style="font-size:11px;color:var(--text3)">'+(c.nascent_evidence||c.evidence||'—')+'</td>' +
      '<td style="font-size:11px;color:var(--text3)">'+(c.nascent_note||c.note||'')+'</td></tr>';
  }).join('');

  // TQ tab
  var tq = td.tq_criteria||[];
  document.getElementById('tc-tq').textContent = tq.length;
  document.querySelector('#res-tq-tbl tbody').innerHTML = tq.map(function(c,i){
    var st=(c.nascent_status||c.status||'REVIEW').toUpperCase().replace(/ /g,'-');
    return '<tr><td style="color:var(--text3)">'+(i+1)+'</td>' +
      '<td style="color:var(--text);font-size:12.5px">'+(c.criterion||c.criteria||c.name||'')+'</td>' +
      '<td style="font-size:11px;color:var(--text2)">'+(c.required_value||c.requirement||c.value||'—')+'</td>' +
      '<td><span class="pill pill-'+st+'">'+st+'</span></td>' +
      '<td style="font-size:11px;color:var(--text3)">'+(c.nascent_evidence||c.evidence||'—')+'</td>' +
      '<td style="font-size:11px;color:var(--text3)">'+(c.nascent_note||c.note||'')+'</td></tr>';
  }).join('');

  // Assessment
  var assess = td.assessment||td.bid_assessment||td.recommendation||'';
  var risks = td.risks||td.risk_factors||[];
  var actions = td.action_plan||td.action_items||[];
  var assHtml = '';
  if (typeof assess === 'string' && assess) assHtml += '<div style="margin-bottom:12px;color:var(--text2);line-height:1.6;font-size:13px">'+assess+'</div>';
  if (risks.length) {
    assHtml += '<div style="font-size:11px;font-weight:600;color:var(--red);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">⚠ Risk Factors</div>';
    assHtml += risks.map(function(r){ return '<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12.5px;color:var(--text2)">• '+(r.description||r.risk||r)+'</div>'; }).join('');
  }
  if (actions.length) {
    assHtml += '<div style="font-size:11px;font-weight:600;color:var(--green);text-transform:uppercase;letter-spacing:.5px;margin:12px 0 8px">✓ Action Plan</div>';
    assHtml += actions.map(function(a,i){ return '<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12.5px;color:var(--text2)">'+(i+1)+'. '+(a.action||a.description||a)+'</div>'; }).join('');
  }
  document.getElementById('res-assessment').innerHTML = assHtml || '<div style="color:var(--text3);font-size:13px">No assessment data</div>';

  // Checklist
  var cl = td.checklist||td.document_checklist||[];
  document.getElementById('tc-cl').textContent = cl.length;
  var clHtml = '';
  if (cl.length) {
    clHtml = '<table class="crit-tbl"><thead><tr><th>#</th><th>Document</th><th>Status</th><th>Note</th></tr></thead><tbody>';
    cl.forEach(function(item,i){
      var st = (item.status||'Prepare');
      var pc = st==='Ready'?'MET':st==='CRITICAL'?'NOT-MET':'CONDITIONAL';
      clHtml += '<tr><td style="color:var(--text3)">'+(i+1)+'</td>' +
        '<td style="color:var(--text);font-size:12.5px">'+(item.document||item.name||'')+'</td>' +
        '<td><span class="pill pill-'+pc+'">'+st+'</span></td>' +
        '<td style="font-size:11.5px;color:var(--text3)">'+(item.hint||item.note||item.nascent_note||'')+'</td></tr>';
    });
    clHtml += '</tbody></table>';
  } else {
    // Parse checklist from string if available
    var clStr = td.checklist_raw||td.raw_checklist||'';
    if (clStr) {
      clHtml = '<div style="font-size:12.5px;color:var(--text2);white-space:pre-wrap;line-height:1.7">'+clStr+'</div>';
    } else {
      clHtml = '<div class="empty"><div class="empty-ico">📋</div><div class="empty-title">No checklist generated</div></div>';
    }
  }
  document.getElementById('res-checklist').innerHTML = clHtml;

  // Pre-bid queries
  var pbq = td.prebid_queries||td.pre_bid_queries||[];
  document.getElementById('tc-pb').textContent = pbq.length;
  if (pbq.length) renderPrebidList(pbq);
  else document.getElementById('prebid-queries-wrap').innerHTML =
    '<div class="empty"><div class="empty-ico">❓</div><div class="empty-title">No pre-bid queries generated</div><div style="margin-top:8px"><button class="btn btn-primary btn-sm" onclick="generatePrebidFromResult()">Generate Pre-bid Queries</button></div></div>';

  // Scope
  var scope = td.scope_of_work||td.scope||'';
  document.getElementById('res-scope').textContent = scope || 'No scope extracted.';

  // Report
  document.getElementById('res-report').innerHTML = buildReportPreview(td, v);

  // Show workflow bar
  document.getElementById('wf-bar').style.display = 'flex';

  // Auto-init BOQ
  if (td.scope_of_work||td.scope) document.getElementById('boq-wrap').dataset.scope = td.scope_of_work||td.scope;

  switchAnTab('pq');
  toast('Analysis complete — Verdict: '+v, v==='BID'?'success':v==='NO-BID'?'error':'info');
}

function buildReportPreview(td, v) {
  var name = td.tender_name||td.brief||'Tender';
  var org  = td.org_name||td.organization||'';
  var html = '<div style="font-family:var(--mono);font-size:12px;line-height:1.8;color:var(--text2);padding:12px 0">';
  html += '<div style="font-size:16px;font-weight:700;color:var(--text);margin-bottom:8px">BID / NO-BID ANALYSIS REPORT</div>';
  html += '<div><b>Tender:</b> '+name+'</div>';
  html += '<div><b>Organisation:</b> '+org+'</div>';
  html += '<div><b>Verdict:</b> <span style="color:'+(v==='BID'?'var(--green)':v==='NO-BID'?'var(--red)':'var(--amber)')+'">'+v+'</span></div>';
  html += '<div style="margin-top:12px"><button class="btn btn-success btn-sm" onclick="downloadTenderDoc()">⬇ Download .docx Report</button></div>';
  html += '</div>';
  return html;
}

function switchAnTab(name) {
  S.anTab = name;
  document.querySelectorAll('.tab-bar .tab').forEach(function(t,i){
    var tabs = ['pq','tq','assessment','checklist','prebid','boq','report','scope'];
    t.classList.toggle('active', tabs[i]===name);
  });
  document.querySelectorAll('[id^="anp-"]').forEach(function(p){ p.classList.remove('active'); });
  var panel = document.getElementById('anp-'+name);
  if (panel) panel.classList.add('active');
}

function renderPrebidList(queries) {
  var html = '';
  if (!queries || !queries.length) {
    document.getElementById('prebid-queries-wrap').innerHTML = '<div class="empty"><div class="empty-ico">❓</div><div class="empty-title">No queries</div></div>';
    return;
  }
  html = '<div style="display:flex;flex-direction:column;gap:8px">';
  queries.forEach(function(q,i) {
    var code = q.query_code||q.code||('PBQ-'+(i+1).toString().padStart(2,'0'));
    var text = q.query_text||q.query||q.text||q;
    var cat = q.category||'';
    html += '<div style="padding:10px 14px;border:1px solid var(--border);border-radius:var(--rsm);background:var(--glass)">' +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">' +
      '<span style="font-family:var(--mono);font-size:11px;color:var(--cyan)">'+code+'</span>' +
      (cat?'<span class="badge badge-purple">'+cat+'</span>':'') +
      '</div>' +
      '<div style="font-size:12.5px;color:var(--text2)">'+text+'</div>' +
      '</div>';
  });
  html += '</div>';
  document.getElementById('prebid-queries-wrap').innerHTML = html;
}

async function generatePrebidFromResult() {
  var tid = S.analysisTid;
  if (!tid) { toast('No tender linked — analyse a tender with T247 ID first','warning'); return; }
  showLoading('Generating pre-bid queries…');
  try {
    var r = await fetch('/prebid-queries/'+tid);
    var d = await r.json();
    hideLoading();
    var q = d.queries||d.prebid_queries||[];
    if (q.length) { renderPrebidList(q); document.getElementById('tc-pb').textContent=q.length; toast(q.length+' queries generated','success'); }
    else toast('No queries generated','warning');
  } catch(e) { hideLoading(); toast('Error: '+e.message,'error'); }
}

async function downloadTenderDoc() {
  var tid = S.analysisTid;
  if (tid) {
    showLoading('Generating report…');
    try {
      var r = await fetch('/tender/'+tid+'/download-report',{method:'POST'});
      var d = await r.json();
      hideLoading();
      if (d.filename) { window.location.href='/download/'+d.filename; return; }
    } catch(e) {}
    hideLoading();
  }
  if (S.analysisFilename) { window.location.href = '/download/'+S.analysisFilename; return; }
  toast('No report available — analyse a tender first','error');
}

/* ── Pre-bid Response Tracker ── */
var _pbrStore = {};

function savePrebidResponse() {
  var qno  = (document.getElementById('pbr-qno')||{}).value.trim();
  var stat = (document.getElementById('pbr-status')||{}).value||'Awaited';
  var note = (document.getElementById('pbr-note')||{}).value.trim();
  if (!qno) { toast('Enter query number','warning'); return; }
  var tid = S.analysisTid||'general';
  if (!_pbrStore[tid]) _pbrStore[tid] = {};
  _pbrStore[tid][qno] = {status:stat, note:note, date:new Date().toLocaleDateString('en-IN')};
  renderPbrList();
  document.getElementById('pbr-qno').value='';
  document.getElementById('pbr-note').value='';
  toast('Response saved','success');
}

function renderPbrList() {
  var el = document.getElementById('pbr-list');
  if (!el) return;
  var tid = S.analysisTid||'general';
  var store = _pbrStore[tid]||{};
  var keys = Object.keys(store);
  if (!keys.length) { el.innerHTML='<div style="font-size:12px;color:var(--text3);padding:8px 0">No responses recorded yet.</div>'; return; }
  var statusColors = {Received:'var(--green)',Awaited:'var(--amber)',Partial:'var(--cyan)','Clarification Needed':'var(--red)'};
  el.innerHTML = '<div style="display:flex;flex-direction:column;gap:6px">' +
    keys.map(function(k){
      var e = store[k];
      var c = statusColors[e.status]||'var(--text2)';
      return '<div style="display:flex;align-items:center;gap:10px;padding:7px 10px;border:1px solid var(--border);border-radius:var(--rsm);font-size:12px">' +
        '<span style="font-family:var(--mono);color:var(--cyan);min-width:80px">'+k+'</span>' +
        '<span style="color:'+c+';min-width:100px;font-weight:600">'+e.status+'</span>' +
        '<span style="flex:1;color:var(--text2)">'+e.note+'</span>' +
        '<span style="color:var(--text3)">'+e.date+'</span>' +
        '<button class="btn btn-ghost btn-xs" onclick="deletePbr(\''+k+'\')">×</button>' +
        '</div>';
    }).join('') + '</div>';
}

function deletePbr(qno) {
  var tid = S.analysisTid||'general';
  if (_pbrStore[tid]) { delete _pbrStore[tid][qno]; renderPbrList(); }
}

/* ── BOQ ── */
function extractBoq() {
  var scope = (document.getElementById('boq-wrap')||{}).dataset.scope || '';
  if (!scope && S.analysis) { var td=S.analysis.tender_data||S.analysis; scope=td.scope_of_work||td.scope||''; }
  if (!scope) { toast('No scope of work found — analyse a tender first','warning'); return; }
  showLoading('Extracting BOQ…');
  fetch('/boq/extract', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:scope})})
    .then(function(r){ return r.json(); }).then(function(d){
      hideLoading();
      S.boqItems = d.items||[];
      renderBoqTable();
    }).catch(function(e){ hideLoading(); toast('BOQ extract: '+e.message,'error'); });
}

function addBoqRow() {
  S.boqItems.push({description:'New Item',category:'Manpower',unit:'Months',qty:1,rate:0});
  renderBoqTable();
}

function renderBoqTable() {
  var wrap = document.getElementById('boq-table-wrap');
  if (!wrap) return;
  if (!S.boqItems.length) { wrap.innerHTML='<div class="empty"><div class="empty-ico">📊</div><div class="empty-title">No BOQ items</div></div>'; return; }
  var html = '<table class="crit-tbl"><thead><tr><th>#</th><th>Description</th><th>Category</th><th>Unit</th><th>Qty</th><th>Rate (₹)</th><th>Amount (₹)</th><th></th></tr></thead><tbody>';
  S.boqItems.forEach(function(item,i){
    var amt = (parseFloat(item.qty)||0)*(parseFloat(item.rate)||0);
    html += '<tr>' +
      '<td style="color:var(--text3)">'+(i+1)+'</td>' +
      '<td><input class="inp" style="min-width:160px" value="'+item.description+'" onchange="S.boqItems['+i+'].description=this.value"></td>' +
      '<td><input class="inp" style="width:120px" value="'+item.category+'" onchange="S.boqItems['+i+'].category=this.value"></td>' +
      '<td><input class="inp" style="width:90px" value="'+item.unit+'" onchange="S.boqItems['+i+'].unit=this.value"></td>' +
      '<td><input class="inp" type="number" style="width:70px" value="'+item.qty+'" onchange="S.boqItems['+i+'].qty=this.value;renderBoqTable()"></td>' +
      '<td><input class="inp" type="number" style="width:100px" value="'+item.rate+'" onchange="S.boqItems['+i+'].rate=this.value;renderBoqTable()"></td>' +
      '<td style="font-family:var(--mono);color:var(--cyan)">₹'+amt.toLocaleString('en-IN')+'</td>' +
      '<td><button class="btn btn-danger btn-xs" onclick="S.boqItems.splice('+i+',1);renderBoqTable()">×</button></td>' +
      '</tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

function calcBoq() {
  var margin = parseInt(document.getElementById('boq-margin').value)||15;
  var base = S.boqItems.reduce(function(s,i){ return s+(parseFloat(i.qty)||0)*(parseFloat(i.rate)||0); },0);
  var marginAmt = base * margin/100;
  var subtotal = base + marginAmt;
  var gst = subtotal * 0.18;
  var grand = subtotal + gst;
  document.getElementById('boq-totals').innerHTML =
    '<div style="background:var(--glass);border:1px solid var(--border);border-radius:var(--rsm);padding:14px 18px;margin-top:8px">' +
    '<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px"><span style="color:var(--text2)">Base Total</span><span style="font-family:var(--mono)">₹'+base.toLocaleString('en-IN')+'</span></div>' +
    '<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px"><span style="color:var(--text2)">Margin ('+margin+'%)</span><span style="font-family:var(--mono)">₹'+marginAmt.toLocaleString('en-IN')+'</span></div>' +
    '<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px"><span style="color:var(--text2)">Subtotal</span><span style="font-family:var(--mono)">₹'+subtotal.toLocaleString('en-IN')+'</span></div>' +
    '<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px"><span style="color:var(--text2)">GST (18%)</span><span style="font-family:var(--mono)">₹'+gst.toLocaleString('en-IN')+'</span></div>' +
    '<div style="height:1px;background:var(--border);margin:8px 0"></div>' +
    '<div style="display:flex;justify-content:space-between;font-size:16px;font-weight:700"><span style="color:var(--text)">Grand Total</span><span style="color:var(--green);font-family:var(--mono)">₹'+grand.toLocaleString('en-IN')+'</span></div>' +
    '</div>';
  toast('BOQ calculated','success');
}

/* ════════════════════════════════════════════════════════
   PIPELINE
════════════════════════════════════════════════════════ */
function loadPipeline() {
  var tenders = S.tenders.length ? S.tenders : S.dashTenders;
  if (!tenders.length) {
    fetch('/tenders').then(function(r){ return r.json(); }).then(function(d){
      S.tenders = Array.isArray(d)?d:(d.tenders||[]);
      renderKanban(S.tenders);
    });
  } else {
    renderKanban(tenders);
  }
}

function renderKanban(tenders) {
  var board = document.getElementById('kanban-board');
  if (!board) return;
  var cols = {};
  STAGES.forEach(function(s){ cols[s]=[]; });
  tenders.forEach(function(t){ var s=t.stage||'Identified'; if(!cols[s]) cols[s]=[]; cols[s].push(t); });

  board.innerHTML = STAGES.map(function(stage){
    var cards = cols[stage]||[];
    var c = STAGE_COLORS[stage]||'#6366f1';
    var html = '<div class="k-col">';
    html += '<div class="k-head" style="border-left:3px solid '+c+'"><span class="k-head-title">'+stage+'</span><span class="k-cnt">'+cards.length+'</span></div>';
    html += cards.slice(0,8).map(function(t){
      var dl = t.bid_submission_date||t.deadline;
      var days = daysLeft(dl);
      var dlColor = days===null?'var(--text3)':days<0?'var(--text3)':days<=3?'var(--red)':days<=7?'var(--amber)':'var(--green)';
      var dlTxt = days===null?'—':days<0?'Expired':days+'d left';
      var tid = t.t247_id||t.id||'';
      return '<div class="k-card" onclick="openTenderModal(\''+tid+'\')">'+
        '<div class="k-card-title">'+(t.tender_name||t.brief||'Unnamed').substring(0,60)+'</div>'+
        '<div class="k-card-meta">'+
          '<span>'+fmtCr(t.estimated_cost)+'</span>'+
          '<span style="color:'+dlColor+';margin-left:auto">'+dlTxt+'</span>'+
        '</div>'+
        (t.verdict?'<div style="margin-top:6px">'+verdictBadge(t.verdict)+'</div>':'')+
        '</div>';
    }).join('');
    if (cards.length>8) html += '<div style="text-align:center;font-size:11px;color:var(--text3);padding:6px">+ '+(cards.length-8)+' more</div>';
    html += '</div>';
    return html;
  }).join('');
}

/* ════════════════════════════════════════════════════════
   CALENDAR
════════════════════════════════════════════════════════ */
function renderCalendar() {
  var y = S.calYear, m = S.calMonth;
  var months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('cal-label').textContent = months[m]+' '+y;

  var tenders = S.tenders.length ? S.tenders : S.dashTenders;
  var dlMap = {};
  tenders.forEach(function(t){
    var dl = t.bid_submission_date||t.deadline||'';
    if (!dl) return;
    var dt = new Date(dl);
    if (isNaN(dt)) return;
    var key = dt.getFullYear()+'-'+(dt.getMonth()+1)+'-'+dt.getDate();
    if (!dlMap[key]) dlMap[key]=[];
    dlMap[key].push(t);
  });

  var grid = document.getElementById('cal-grid');
  var firstDay = new Date(y,m,1).getDay();
  var daysInMonth = new Date(y,m+1,0).getDate();
  var today = new Date();

  var cells = '';
  var dayCount = 1;
  var totalCells = Math.ceil((firstDay+daysInMonth)/7)*7;
  for (var i=0;i<totalCells;i++) {
    var isOtherMonth = i<firstDay || dayCount>daysInMonth;
    var cellDay = isOtherMonth?'':dayCount;
    var isToday = !isOtherMonth && today.getFullYear()===y && today.getMonth()===m && today.getDate()===dayCount;
    var key = y+'-'+(m+1)+'-'+dayCount;
    var thisDl = (!isOtherMonth && dlMap[key])||[];

    var dots = '';
    if (thisDl.length) {
      dots = '<div class="cal-dots">' + thisDl.slice(0,4).map(function(t){
        var days = daysLeft(t.bid_submission_date||t.deadline);
        var cls = days!==null&&days<=3?'dot-hot':days!==null&&days<=7?'dot-soon':'dot-ok';
        return '<div class="cal-dot '+cls+'" title="'+(t.tender_name||t.brief||'').substring(0,40)+'"></div>';
      }).join('') + (thisDl.length>4?'<div style="font-size:8px;color:var(--text3)">+'+( thisDl.length-4)+'</div>':'') + '</div>';
    }

    var extraCls = isOtherMonth?' dim':'';
    if (isToday) extraCls += ' today';
    var onclick = isOtherMonth?'':'onclick="calSelectDay('+dayCount+',\''+(key)+'\')"';
    cells += '<div class="cal-cell'+extraCls+'" '+onclick+'>';
    if (!isOtherMonth) cells += '<span class="cal-date">'+dayCount+'</span>'+dots;
    cells += '</div>';
    if (!isOtherMonth) dayCount++;
  }
  grid.innerHTML = cells;
}

function calSelectDay(day, key) {
  var tenders = S.tenders.length ? S.tenders : S.dashTenders;
  var thisDl = tenders.filter(function(t){
    var dl = t.bid_submission_date||t.deadline||'';
    if (!dl) return false;
    var dt = new Date(dl);
    if (isNaN(dt)) return false;
    return (dt.getFullYear()+'-'+(dt.getMonth()+1)+'-'+dt.getDate()) === key;
  });
  var detail = document.getElementById('cal-detail');
  var list = document.getElementById('cal-detail-list');
  var dateEl = document.getElementById('cal-detail-date');
  if (!detail||!list) return;
  if (!thisDl.length) { detail.style.display='none'; return; }
  detail.style.display='block';
  dateEl.textContent = day+' '+['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][S.calMonth]+' '+S.calYear+' — '+thisDl.length+' deadline'+(thisDl.length>1?'s':'');
  list.innerHTML = thisDl.map(function(t){
    return '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);font-size:12.5px">' +
      '<div style="flex:1;color:var(--text)">'+(t.tender_name||t.brief||'Unnamed').substring(0,70)+'</div>' +
      '<div style="color:var(--text2)">'+(t.org_name||t.organization||'')+'</div>' +
      verdictBadge(t.verdict) +
      '<button class="btn btn-primary btn-xs" onclick="loadTenderAnalysis(\''+(t.t247_id||t.id||'')+'\')">Analyse</button>' +
      '</div>';
  }).join('');
}

function calPrev() { S.calMonth--; if(S.calMonth<0){S.calMonth=11;S.calYear--;} renderCalendar(); }
function calNext() { S.calMonth++; if(S.calMonth>11){S.calMonth=0;S.calYear++;} renderCalendar(); }
function calToday() { var d=new Date(); S.calYear=d.getFullYear(); S.calMonth=d.getMonth(); renderCalendar(); }

/* ════════════════════════════════════════════════════════
   TENDER MODAL
════════════════════════════════════════════════════════ */
function openTenderModal(tid) {
  if (!tid) return;
  var t = S.tenders.find(function(x){ return (x.t247_id||x.id)==tid; }) ||
          S.dashTenders.find(function(x){ return (x.t247_id||x.id)==tid; });
  if (!t) { toast('Tender not found in local data','warning'); return; }

  document.getElementById('modal-tender-title').textContent = (t.tender_name||t.brief||'Tender Details').substring(0,60);
  var checklist = t.checklist||'';
  var checklistHtml = '';
  if (checklist) {
    var items = checklist.match(/\d+\.\s.+/g)||checklist.split('\n').filter(function(s){ return s.trim(); });
    if (items.length>1) {
      checklistHtml = '<div style="margin-top:14px"><div style="font-size:11px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Document Checklist</div>' +
        '<div style="max-height:200px;overflow-y:auto;display:flex;flex-direction:column;gap:4px">' +
        items.slice(0,30).map(function(item){
          return '<div style="display:flex;gap:8px;font-size:12px;color:var(--text2);padding:4px 0;border-bottom:1px solid var(--border)"><span style="color:var(--primary);flex-shrink:0">✓</span><span>'+item.replace(/^\d+\.\s*/,'')+'</span></div>';
        }).join('') +
        (items.length>30?'<div style="font-size:11px;color:var(--text3);padding:4px 0">+ '+(items.length-30)+' more items…</div>':'') +
        '</div></div>';
    }
  }

  document.getElementById('modal-tender-body').innerHTML =
    '<div class="kpi-row">' +
      '<div class="kpi"><div class="kpi-v">'+fmtCr(t.estimated_cost)+'</div><div class="kpi-l">Est. Cost</div></div>' +
      '<div class="kpi"><div class="kpi-v">'+fmtCr(t.emd)+'</div><div class="kpi-l">EMD</div></div>' +
      '<div class="kpi"><div class="kpi-v">'+dlChip(t.bid_submission_date||t.deadline)+'</div><div class="kpi-l">Deadline</div></div>' +
    '</div>' +
    '<div style="display:flex;flex-direction:column;gap:8px;font-size:13px;margin-top:12px">' +
      '<div><span style="color:var(--text3)">Ref No: </span><span style="font-family:var(--mono);color:var(--text2)">'+(t.ref_no||t.reference_no||'—')+'</span></div>' +
      '<div><span style="color:var(--text3)">Organisation: </span><span style="color:var(--text)">'+(t.org_name||t.organization||'—')+'</span></div>' +
      '<div><span style="color:var(--text3)">Location: </span><span style="color:var(--text2)">'+(t.state||t.location||'—')+'</span></div>' +
      '<div><span style="color:var(--text3)">MSME: </span><span style="color:var(--green)">'+(t.msme_exempt||t.msme_exemption||'—')+'</span></div>' +
      '<div><span style="color:var(--text3)">Stage: </span>'+stageBadge(t.stage||'Identified')+'</div>' +
      '<div><span style="color:var(--text3)">Verdict: </span>'+verdictBadge(t.verdict)+'</div>' +
      (t.brief||t.tender_name?'<div style="margin-top:6px"><span style="color:var(--text3)">Brief: </span><span style="color:var(--text2);line-height:1.5">'+(t.brief||t.tender_name||'').substring(0,200)+'</span></div>':'') +
    '</div>' +
    checklistHtml +
    '<div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap">' +
      '<button class="btn btn-primary btn-sm" onclick="loadTenderAnalysis(\''+(t.t247_id||t.id||'')+'\');closeModal(\'modal-tender\')">🔍 Analyse</button>' +
      '<button class="btn btn-ghost btn-sm" onclick="openStageModal(\''+(t.t247_id||t.id||'')+'\',\''+(t.tender_name||t.brief||'').replace(/'/g,''').substring(0,40)+'\');closeModal(\'modal-tender\')">📊 Update Stage</button>' +
    '</div>';

  openModal('modal-tender');
}

/* ════════════════════════════════════════════════════════
   STAGE MODAL
════════════════════════════════════════════════════════ */
function openStageModal(tid, name) {
  S.stageTid = tid;
  document.getElementById('stage-tender-name').textContent = name||tid;
  var t = S.tenders.find(function(x){ return (x.t247_id||x.id)==tid; });
  if (t && t.stage) {
    var sel = document.getElementById('stage-sel');
    if (sel) sel.value = t.stage;
  }
  document.getElementById('stage-ref-wrap').style.display='none';
  openModal('modal-stage');
}

document.getElementById && document.getElementById('stage-sel') && document.getElementById('stage-sel').addEventListener('change', function(){
  var show = ['Submitted','Won'].includes(this.value);
  document.getElementById('stage-ref-wrap').style.display = show?'block':'none';
});

async function saveStageModal() {
  var tid = S.stageTid;
  var stage = document.getElementById('stage-sel').value;
  var ref = document.getElementById('stage-ref').value.trim();
  if (!tid) return;
  try {
    var body = {stage:stage};
    if (ref) body.ref_no = ref;
    await fetch('/tender/'+tid+'/stage', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    toast('Stage updated: '+stage,'success');
    closeModal('modal-stage');
    // Update local state
    [S.tenders, S.dashTenders].forEach(function(arr){
      var t = arr.find(function(x){ return (x.t247_id||x.id)==tid; });
      if (t) t.stage = stage;
    });
    if (S.currentPage==='pipeline') renderKanban(S.tenders);
    else renderDash();
  } catch(e) { toast('Error: '+e.message,'error'); }
}

/* Workflow shortcuts from analyse page */
async function markApprovalPending() {
  var tid = S.analysisTid;
  if (!tid) { toast('No T247 ID linked — enter T247 ID before analysis','warning'); return; }
  try {
    await fetch('/tender/'+tid+'/stage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage:'Approval Pending'})});
    toast('✅ Moved to Approval Pending','success');
    loadDashboard();
  } catch(e){ toast('Error: '+e.message,'error'); }
}

async function markSubmitted() {
  var tid = S.analysisTid;
  if (!tid) { toast('No T247 ID linked','warning'); return; }
  var ref = prompt('Enter submission reference number (optional):');
  try {
    var body = {stage:'Submitted'};
    if (ref) body.ref_no = ref;
    await fetch('/tender/'+tid+'/stage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    toast('📤 Marked as Submitted'+(ref?' — Ref: '+ref:''),'success');
    loadDashboard();
  } catch(e){ toast('Error: '+e.message,'error'); }
}

/* ════════════════════════════════════════════════════════
   SETTINGS
════════════════════════════════════════════════════════ */
async function loadSettings() {
  try {
    var r = await fetch('/config');
    var d = await r.json();
    var map = {
      'cfg-gemini':'gemini_api_key','cfg-groq':'groq_api_key',
      'cfg-company':'company_name','cfg-gst':'gst_number',
      'cfg-turnover':'annual_turnover_cr','cfg-sectors':'preferred_sectors',
      'cfg-min-val':'min_project_value_cr','cfg-max-val':'max_project_value_cr',
      'cfg-dnb':'do_not_bid_keywords','cfg-preferred':'preferred_keywords',
    };
    Object.keys(map).forEach(function(id){
      var el=document.getElementById(id);
      if (!el) return;
      var val=d[map[id]]||(d.bid_rules&&d.bid_rules[map[id]])||'';
      if (Array.isArray(val)) val=val.join(', ');
      el.value=val;
    });
    if (d.msme_registered!==undefined) {
      var msmeEl = document.getElementById('cfg-msme');
      if (msmeEl) msmeEl.value = d.msme_registered?'yes':'no';
    }
    var lastEl = document.getElementById('import-last');
    if (lastEl && d.last_import) lastEl.textContent = d.last_import;
  } catch(e){ toast('Could not load settings','warning'); }
}

async function saveSettings() {
  showLoading('Saving…');
  function split(id){ return ((document.getElementById(id)||{}).value||'').split(',').map(function(x){return x.trim();}).filter(Boolean); }
  function val(id){ return ((document.getElementById(id)||{}).value||'').trim(); }

  var body = {
    gemini_api_key: val('cfg-gemini'),
    groq_api_key: val('cfg-groq'),
    company_name: val('cfg-company'),
    gst_number: val('cfg-gst'),
    annual_turnover_cr: parseFloat(val('cfg-turnover'))||0,
    msme_registered: (document.getElementById('cfg-msme')||{}).value==='yes',
    bid_rules: {
      preferred_sectors: split('cfg-sectors'),
      min_project_value_cr: parseFloat(val('cfg-min-val'))||0.5,
      max_project_value_cr: parseFloat(val('cfg-max-val'))||50,
      do_not_bid: split('cfg-dnb'),
      preferred_keywords: split('cfg-preferred'),
    }
  };

  try {
    var r = await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var d = await r.json();
    hideLoading();
    toast(d.status==='saved'?'Settings saved ✓':'Saved locally','success');
  } catch(e){ hideLoading(); toast('Error: '+e.message,'error'); }
}

async function testApiKeys() {
  var el = document.getElementById('key-test-result');
  if (el) el.textContent = 'Testing…';
  try {
    var r = await fetch('/api/key-usage-map');
    var d = await r.json();
    if (el) el.innerHTML = '<span style="color:var(--green)">✓ Keys loaded — '+JSON.stringify(d).substring(0,80)+'</span>';
  } catch(e) { if(el) el.innerHTML='<span style="color:var(--red)">✗ '+e.message+'</span>'; }
}

function importExcel(input) {
  var file = input.files[0];
  if (!file) return;
  var statusEl = document.getElementById('import-status');
  if (statusEl) statusEl.textContent = 'Uploading…';
  var fd = new FormData();
  fd.append('file', file);
  showLoading('Importing '+file.name+'…');
  fetch('/import-excel',{method:'POST',body:fd}).then(function(r){ return r.json(); }).then(function(d){
    hideLoading();
    if (statusEl) statusEl.innerHTML = '<span style="color:var(--green)">✓ '+(d.imported||d.added||0)+' imported</span>';
    toast('Imported: '+(d.imported||d.added||0)+' tenders','success');
    var lastEl = document.getElementById('import-last');
    if (lastEl) lastEl.textContent = new Date().toLocaleDateString('en-IN');
    // Reload tenders
    S.tenders = [];
    loadDashboard();
  }).catch(function(e){ hideLoading(); if(statusEl) statusEl.innerHTML='<span style="color:var(--red)">✗ '+e.message+'</span>'; toast('Import error: '+e.message,'error'); });
  input.value='';
}

function openImportModal() {
  document.getElementById('import-file').click();
}

function exportTendersCsv() {
  var rows = S.tenders;
  if (!rows.length) { toast('No tenders loaded','warning'); return; }
  var headers = ['T247_ID','Brief','Org','Location','Cost','Deadline','Verdict','Stage','MSME'];
  var csv = headers.join(',') + '\n';
  csv += rows.map(function(t){
    return [t.t247_id||'',
      '"'+(t.tender_name||t.brief||'').replace(/"/g,'""').substring(0,60)+'"',
      '"'+(t.org_name||t.organization||'').replace(/"/g,'""')+'"',
      t.state||t.location||'',
      t.estimated_cost||'',
      t.bid_submission_date||t.deadline||'',
      t.verdict||'',
      t.stage||'',
      t.msme_exempt||t.msme_exemption||''].join(',');
  }).join('\n');
  var blob = new Blob([csv], {type:'text/csv'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'tenders-'+new Date().toISOString().slice(0,10)+'.csv';
  a.click();
  toast('CSV exported','success');
}

/* ════════════════════════════════════════════════════════
   CHATBOT
════════════════════════════════════════════════════════ */
var _chatHistory = [];

function toggleChat() {
  var panel = document.getElementById('chat-panel');
  panel.classList.toggle('open');
}

async function sendChat() {
  var inp = document.getElementById('chat-inp');
  var msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';

  appendChatMsg(msg, 'user');
  var tid = S.analysisTid||'';
  _chatHistory.push({role:'user', content:msg});

  try {
    var body = {message:msg, tender_id:tid, history:_chatHistory.slice(-6)};
    var r = await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var d = await r.json();
    var reply = d.reply||d.message||d.response||'Sorry, no response.';
    appendChatMsg(reply, 'ai');
    _chatHistory.push({role:'assistant', content:reply});
  } catch(e) { appendChatMsg('Error: '+e.message,'ai'); }
}

function appendChatMsg(text, role) {
  var msgs = document.getElementById('chat-msgs');
  if (!msgs) return;
  var el = document.createElement('div');
  el.className = 'cmsg ' + role;
  el.textContent = text;
  msgs.appendChild(el);
  msgs.scrollTop = msgs.scrollHeight;
}

/* ════════════════════════════════════════════════════════
   PAGINATION HELPER
════════════════════════════════════════════════════════ */
function renderPager(containerId, totalPages, currentPage, onClick) {
  var el = document.getElementById(containerId);
  if (!el || totalPages <= 1) { if(el) el.innerHTML=''; return; }
  var html = '<div class="pager">';
  html += '<div class="pg" onclick="javascript:void(0)" id="'+containerId+'-prev">‹</div>';
  var start = Math.max(1, currentPage-2);
  var end = Math.min(totalPages, start+4);
  if (start>1) html += '<div class="pg" data-p="1">1</div>' + (start>2?'<span class="pg-info">…</span>':'');
  for (var p=start;p<=end;p++) html += '<div class="pg'+(p===currentPage?' cur':'')+'" data-p="'+p+'">'+p+'</div>';
  if (end<totalPages) html += (end<totalPages-1?'<span class="pg-info">…</span>':'') + '<div class="pg" data-p="'+totalPages+'">'+totalPages+'</div>';
  html += '<div class="pg" id="'+containerId+'-next">›</div>';
  html += '<span class="pg-info">'+currentPage+' / '+totalPages+'</span>';
  html += '</div>';
  el.innerHTML = html;
  el.querySelectorAll('.pg[data-p]').forEach(function(btn){
    btn.addEventListener('click', function(){ onClick(parseInt(this.dataset.p)); });
  });
  var prev = el.querySelector('#'+containerId+'-prev');
  var next = el.querySelector('#'+containerId+'-next');
  if(prev) prev.addEventListener('click', function(){ if(currentPage>1) onClick(currentPage-1); });
  if(next) next.addEventListener('click', function(){ if(currentPage<totalPages) onClick(currentPage+1); });
}

/* ════════════════════════════════════════════════════════
   STAGE SEL LISTENER (deferred)
════════════════════════════════════════════════════════ */
function _initStageSelListener() {
  var sel = document.getElementById('stage-sel');
  if (sel) {
    sel.addEventListener('change', function(){
      var show = ['Submitted','Won'].includes(this.value);
      document.getElementById('stage-ref-wrap').style.display = show?'block':'none';
    });
  }
}

/* ════════════════════════════════════════════════════════
   INIT
════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function() {
  _initStageSelListener();
  loadDashboard();
  // Close modals on overlay click
  document.querySelectorAll('.overlay').forEach(function(ov){
    ov.addEventListener('click', function(e){
      if (e.target === ov) ov.classList.remove('on');
    });
  });
  // Keyboard shortcuts
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape') {
      document.querySelectorAll('.overlay.on').forEach(function(ov){ ov.classList.remove('on'); });
      var cp = document.getElementById('chat-panel');
      if (cp && cp.classList.contains('open')) cp.classList.remove('open');
    }
  });
});

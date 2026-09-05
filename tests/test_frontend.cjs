const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const code = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/initialize\(\);\s*$/, '');

function fixture() {
  const nodes = new Map();
  const classList = () => {
    const set = new Set();
    return {add: (...names) => names.forEach(name => set.add(name)), remove: (...names) => names.forEach(name => set.delete(name)),
      contains: name => set.has(name), toggle(name, enabled) { const value = enabled ?? !set.has(name); value ? set.add(name) : set.delete(name); return value; }};
  };
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value: '', textContent: '', dataset: {}, style: {}, disabled: false, classList: classList(),
      reset() {}, focus() {}, append() {}, scrollIntoView() {}, setAttribute() {}, removeAttribute() {}, closest: () => get(selector + '-label'), querySelector: get, querySelectorAll: () => []});
    return nodes.get(selector);
  };
  const context = { document: {hidden: false, querySelector: get, querySelectorAll: () => [], createElement: get, body: {classList: classList()}},
    window: {location: {href: 'http://127.0.0.1:8765/'}, confirm: () => true, setTimeout() {}},
    URL, TextEncoder, crypto: webcrypto, calls: [], console,
    fetch: async () => ({ok: true, json: async () => ({ok: true})}),
    localStorage: {getItem: () => null, setItem() {}},
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  vm.runInContext(`
    state.activeId = 'a'; state.connected = true;
    state.servers = [{id:'a',name:'Instance A'}, {id:'b',name:'Instance B'}];
    loadCommandHistory = () => {}; renderServerList = () => {}; renderFiles = () => {};
    refreshCurrentView = async () => {}; toast = () => {};
    const originalApi = api;
    const originalRefreshStatus = refreshStatus;
    api = async (path, options) => {
      calls.push({path,options});
      return path.startsWith('/api/file/content')
        ? {data:{path:'server.properties',content:'motd=INSTANCE_A',revision:'a'.repeat(64)}}
        : {message:'ok',revision:'b'.repeat(64),data:{path:'',entries:[],parent:''}};
    };
    refreshStatus = async () => {};
  `, context);
  return {context, nodes, run: input => vm.runInContext(input, context), get};
}

test('switching instances closes the editor and cannot save A into B', async () => {
  const f = fixture();
  await f.run("openTextFile('server.properties')");
  await f.run("selectServer('b')");
  await f.run('saveTextFile()');
  assert.equal(f.run('state.activeId'), 'b');
  assert.equal(f.run('state.editingFile'), null);
  assert.equal(f.get('#file-editor-content').value, '');
  assert.equal(f.context.calls.filter(call => call.path === '/api/file/save').length, 0);
});

test('declining to discard changes keeps the original instance and draft', async () => {
  const f = fixture();
  await f.run("openTextFile('server.properties')");
  f.get('#file-editor-content').value = 'unsaved draft';
  f.context.window.confirm = () => false;
  await f.run("selectServer('b')");
  assert.equal(f.run('state.activeId'), 'a');
  assert.equal(f.get('#file-editor-content').value, 'unsaved draft');
  assert.equal(f.context.calls.filter(call => call.path === '/api/servers/select').length, 0);
});

test('a late file response cannot reopen the previous instance editor', async () => {
  const f = fixture();
  let resolve;
  f.context.deferred = new Promise(done => { resolve = done; });
  f.run('api = () => deferred');
  const opening = f.run("openTextFile('server.properties')");
  f.run("state.activeId = 'b'; resetFileEditor()");
  resolve({data: {path:'server.properties', content:'old content'}});
  await opening;
  assert.equal(f.run('state.editingFile'), null);
  assert.equal(f.get('#file-editor-content').value, '');
});

test('a file save uses its bound instance and original revision', async () => {
  const f = fixture();
  await f.run("openTextFile('server.properties')");
  f.get('#file-editor-content').value = 'motd=updated';
  await f.run('saveTextFile()');
  const save = f.context.calls.find(call => call.path === '/api/file/save');
  assert.equal(save.options.body.server_id, 'a');
  assert.equal(save.options.body.revision, 'a'.repeat(64));
  assert.equal(save.options.body.content, 'motd=updated');
  assert.equal(f.run('editorIsDirty()'), false);
  assert.equal(f.run('state.editingFile.revision'), 'b'.repeat(64));
});

test('API discards a response targeting an instance that is no longer active', async () => {
  const f = fixture();
  let resolve;
  f.context.fetch = () => new Promise(done => { resolve = done; });
  const request = f.run("originalApi('/api/config?server_id=a')");
  f.run("state.activeId = 'b'");
  resolve({ok:true, json:async () => ({ok:true,data:{motd:'old instance'}})});
  await assert.rejects(request, /实例已切换/);
});

test('late status responses cannot replace a newer status', async () => {
  const f = fixture();
  const pending = [];
  f.context.queueApi = () => new Promise(done => pending.push(done));
  f.run('api = queueApi; updateStatusUI = () => {}');
  const first = f.run('originalRefreshStatus()');
  const second = f.run('originalRefreshStatus()');
  pending[1]({data: {running:true,marker:'new'}});
  await second;
  pending[0]({data: {running:false,marker:'old'}});
  await first;
  assert.equal(f.run('state.status.marker'), 'new');
});

test('connection failure marks data stale and exposes the recovery banner', async () => {
  const f = fixture();
  f.run("state.status = {running:true}; api = async () => { throw new Error('offline'); }");
  await f.run('originalRefreshStatus()');
  assert.equal(f.run('state.connected'), false);
  assert.equal(f.context.document.body.classList.contains('disconnected'), true);
  assert.equal(f.get('#connection-banner').classList.contains('hidden'), false);
  assert.match(f.get('#hero-title').textContent, /中断/);
});

test('online mode enabled displays an enabled label', () => {
  const f = fixture();
  f.run('updateStatusUI({running:false,ready:false,online_mode:true})');
  assert.equal(f.get('#detail-online-mode').textContent, '已开启');
});

test('instance header remains identified before its status is available', () => {
  const f = fixture();
  f.run('state.connected = false; renderWorkspaceState()');
  assert.equal(f.get('#server-title').textContent, 'Instance A');
  assert.equal(f.get('#copy-address').disabled, true);
});

test('failed maintenance remains visible on the overview', () => {
  const f = fixture();
  f.run("updateStatusUI({running:true,last_job:{state:'error',type:'backup',message:'Please execute save-on'}})");
  assert.equal(f.get('#status-notice').classList.contains('hidden'), false);
  assert.match(f.get('#notice-detail').textContent, /save-on/);
});

test('metric bars use CSS properties compatible with the strict CSP', () => {
  const f = fixture();
  f.context.document.createElement = () => ({style: {}});
  const bars = [];
  f.context.chart = {replaceChildren: (...elements) => bars.push(...elements)};
  f.run("renderMetricBars(chart, [{at:'2026-09-05',running:true,cpu:25},{at:'2026-09-05',running:false,cpu:0}], 'cpu', 100, String)");
  assert.equal(bars[0].style.height, '25.00%');
  assert.equal(bars[1].style.height, '1.00%');
  assert.equal(bars[1].className, 'offline');
});

test('reconnect retries bootstrap when no instance was loaded', async () => {
  const f = fixture();
  f.run("state.activeId = null; bootstrap = async () => { calls.push({path:'bootstrap-retry'}); }");
  await f.run('reconnectPanel()');
  assert.equal(f.context.calls.at(-1).path, 'bootstrap-retry');
});

function importFixture() {
  const f = fixture();
  const values = {setup_mode:'quick', archive_launch:'auto|', archive_java:'java', archive_xms:'1G', archive_xmx:'6G',
    destination:'C:/test/new-server', archive_name:'Test', server_port:'25565', max_players:'20', view_distance:'6',
    simulation_distance:'5', gamemode:'survival', difficulty:'normal'};
  const elements = Object.fromEntries(Object.entries(values).map(([key,value]) => [key,{value}]));
  for (const key of ['accept_eula','trust_pack','start_after_import','online_mode','secure_profile','allow_flight','whitelist'])
    elements[key] = {checked:['start_after_import','online_mode','secure_profile'].includes(key)};
  f.get('#import-form').elements = elements;
  f.get('#archive-path').value = 'C:/packs/test.zip';
  f.run("state.importInspection = {path:'C:/packs/test.zip'}");
  return f;
}

test('one click sends one asynchronous start request for the selected instance', async () => {
  const f=fixture();
  await f.run("runAction('start')");
  assert.equal(f.context.calls.length,1);
  assert.equal(f.context.calls[0].path,'/api/action');
  assert.equal(f.context.calls[0].options.body.action,'start');
  assert.equal(f.context.calls[0].options.body.server_id,'a');
  assert.equal(f.run('state.pendingAction'),null);
});

test('quick setup hides manual Java and launch selectors, custom mode restores them', () => {
  const f=importFixture();
  f.run("switchImportSource('archive')");
  assert.equal(f.get('#archive-java').disabled,true);
  assert.equal(f.get('#archive-java-label').classList.contains('hidden'),true);
  f.get('#import-form').elements.setup_mode.value='custom';
  f.run('updateQuickSetup()');
  assert.equal(f.get('#archive-java').disabled,false);
  assert.equal(f.get('#archive-java-label').classList.contains('hidden'),false);
  f.run("state.importJobId='j'; updateQuickSetup()");
  assert.equal(f.get('#archive-java').disabled,true);
});

test('consent remains explicit and archive fields stop being required on the FTB tab', () => {
  const f=importFixture();
  f.run("switchImportSource('archive')");
  assert.equal(f.get('#import-form').elements.accept_eula.checked,false);
  assert.equal(f.get('#import-form').elements.trust_pack.checked,false);
  assert.equal(f.get('#import-form').elements.accept_eula.required,true);
  f.run("switchImportSource('ftb')");
  assert.equal(f.get('#import-form').elements.accept_eula.required,false);
  assert.equal(f.get('#import-submit').textContent,'下载并一键开服');
});

test('archive quick import preserves consent values and requests automatic preparation', async () => {
  const f=importFixture();
  f.run("api = async (path, options) => { calls.push({path,options}); return {job:{id:'j',state:'queued'}}; }; waitForImport=async id=>({id});");
  await f.run('importArchive()');
  const body=f.context.calls[0].options.body;
  assert.equal(body.auto_setup,true);
  assert.equal(body.accept_eula,false);
  assert.equal(body.trust_pack,false);
  assert.equal(body.start_after_import,true);
  assert.equal(body.launch_mode,'auto');
  assert.equal(body.properties['online-mode'],true);
});

test('custom mode still prepares a target explicitly labelled automatic', async () => {
  const f=importFixture();
  f.get('#import-form').elements.setup_mode.value='custom';
  f.run("api = async (path, options) => { calls.push({path,options}); return {job:{id:'j',state:'queued'}}; }; waitForImport=async()=>({});");
  await f.run('importArchive()');
  assert.equal(f.context.calls[0].options.body.auto_setup,true);
  f.get('#import-form').elements.archive_launch.value='script|start.bat';
  await f.run('importArchive()');
  assert.equal(f.context.calls[1].options.body.auto_setup,false);
});

test('job polling requests the exact task and does not mistake another completed job for success', async () => {
  const f=fixture();
  f.context.window.setTimeout=resolve=>resolve();
  f.run("api=async path=>{calls.push({path}); return {data:[{id:'other',state:'done'},{id:'j',state:'error',error:'installer failed'}]};}");
  await assert.rejects(f.run("waitForImport('j')"),/installer failed/);
  assert.equal(f.context.calls[0].path,'/api/jobs?id=j');
  assert.equal(f.get('#cancel-import').disabled,true);
});

test('cancellation only addresses the named startup job', async () => {
  const f=fixture();
  await f.run("cancelStartup('job-1')");
  assert.equal(f.context.calls[0].path,'/api/start/cancel');
  assert.equal(f.context.calls[0].options.body.job_id,'job-1');
  await f.run('cancelStartup(null)');
  assert.equal(f.context.calls.length,1);
});

test('environment preparation is visible before a Java game process exists', () => {
  const f=fixture();
  f.run("updateStatusUI({running:false,ready:false,active_job:{id:'j',type:'start',state:'running',phase:'preparing',message:'Downloading Java 8'}})");
  assert.equal(f.get('#startup-task').classList.contains('hidden'),false);
  assert.match(f.get('#startup-task-title').textContent,/准备环境/);
  assert.equal(f.get('#startup-task-detail').textContent,'Downloading Java 8');
  assert.equal(f.get('#cancel-start').disabled,false);
  assert.match(f.get('#hero-title').textContent,/一键开服/);
});

test('loading phase and pending cancellation remain distinct from ready', () => {
  const f=fixture();
  f.run("updateStatusUI({running:true,ready:false,active_job:{id:'j',type:'start',state:'running',phase:'loading',cancel_requested:true}})");
  assert.match(f.get('#startup-task-title').textContent,/正在加载游戏服/);
  assert.equal(f.get('#cancel-start').disabled,true);
  f.run("updateStatusUI({running:true,ready:true,active_job:null})");
  assert.equal(f.get('#startup-task').classList.contains('hidden'),true);
  assert.equal(f.get('#hero-title').textContent,'服务器正在运行。');
});

test('a failed installer is reported even without a Java process exit record', () => {
  const f=fixture();
  f.run("updateStatusUI({running:false,last_job:{state:'error',type:'start',message:'Official installer unavailable'}})");
  assert.equal(f.get('#status-notice').classList.contains('hidden'),false);
  assert.match(f.get('#notice-detail').textContent,/Official installer unavailable/);
});

'use strict';
(() => {
  const el = id => document.getElementById(id);
  let project = null, loading = false, signature = '', sending = false;
  async function call(path, body) {
    const opt = {credentials:'same-origin'};
    if (body !== undefined) Object.assign(opt,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const response = await fetch('/api/missions/' + path, opt);
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
    return data;
  }
  function render(data) {
    const list = el('chatmessages');
    const sig = JSON.stringify(data);
    if (sig === signature) return;
    signature = sig;
    const bottom = list.scrollHeight-list.scrollTop-list.clientHeight < 60;
    list.replaceChildren();
    if (!data.messages.length) {
      const p=document.createElement('p');p.className='note';
      p.textContent='Здесь можно говорить с моделью о выбранном проекте. Она видит его план, рабочий код и результаты проверок.';list.append(p);
    }
    for (const m of data.messages) {
      const user=document.createElement('div');user.className='event';
      const label=document.createElement('small');label.textContent='Ты · '+({direct:'поручение',ask:'обсуждение',try:'запуск программы'}[m.mode]||m.mode)+' · '+new Date(m.created*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
      const text=document.createElement('div');text.style.whiteSpace='pre-wrap';text.textContent=m.text;user.append(label,text);list.append(user);
      const reply=document.createElement('div');reply.className='event';reply.style.paddingLeft='16px';
      const name=document.createElement('small');name.textContent='Локальная модель';
      const content=document.createElement('div');content.style.whiteSpace='pre-wrap';
      content.textContent=m.reply||(m.status==='answering'?'Отвечает…':'Сообщение в очереди…');
      if (m.status==='error') content.className='warning';
      reply.append(name,content);
      if(m.evidence){
        const facts=document.createElement('small');
        facts.textContent='Факты на момент ответа: '+(m.evidence.stable_version||'рабочей версии ещё нет')+
          ' · проверок: '+m.evidence.tests_count+' · изменений кода: '+m.evidence.code_changes;
        reply.append(facts);
      }
      if(m.execution){
        const box=document.createElement('details');box.open=true;
        const heading=document.createElement('summary');heading.textContent='Фактический запуск · '+m.execution.version;
        const output=document.createElement('pre');output.className='logs';output.style.maxHeight='320px';
        output.textContent=JSON.stringify(m.execution,null,2);box.append(heading,output);reply.append(box);
      }
      list.append(reply);
    }
    if (bottom) list.scrollTop=list.scrollHeight;
    const states={none:'Активного поручения нет. Автопилот следует общему плану.',
      queued:'Поручение сохранено и ждёт следующего раунда.',active:'Поручение используется как приоритет текущего шага.',
      used:'По поручению принята версия; автопилот вернулся к общему плану. Проверь результат.',
      deferred:'Поручение отложено после неудачных попыток и не выполнено. Уточни его.'};
    el('chatnotice').textContent=(states[data.priority_status]||states.none)+' Ctrl/⌘ + Enter — отправить.';
    el('clearpriority').disabled=!['queued','active'].includes(data.priority_status);
  }
  window.rcpChat = async mid => {
    if (typeof current !== 'undefined' && current?.config?.id === mid) window.rcpColonyUpdate?.(current);
    if (project!==mid) { project=mid;signature='';el('chatmessages').replaceChildren();el('chattext').value=''; }
    el('chatpanel').classList.remove('hidden');
    if (loading) return;
    loading=true;
    try { const data=await call(mid+'/messages');if(project===mid)render(data); }
    catch(e) { if(project===mid)el('chatnotice').textContent=e.message; }
    finally { loading=false; }
  };
  el('clearpriority').onclick=async()=>{
    if(!project)return;
    try{await call(project+'/guidance/clear',{});await window.rcpChat(project);}
    catch(e){el('chatnotice').textContent=e.message;}
  };
  el('chatmode').onchange=()=>{el('chattext').placeholder=el('chatmode').value==='try'
    ?'Например: посчитай статистику для чисел 3, 7 и -2. Можно также вставить точный JSON.'
    :'Что уже работает? Или задай следующий шаг развития.';};
  el('chatform').addEventListener('submit',async event=>{
    event.preventDefault();
    const text=el('chattext').value.trim(), mid=project;
    if(!text||!mid||sending)return;
    sending=true;el('chatsend').disabled=true;
    try {
      await call(mid+'/messages',{text,mode:el('chatmode').value});
      if(project===mid){el('chattext').value='';await window.rcpChat(mid);}
    } catch(e){el('chatnotice').textContent=e.message;}
    finally{sending=false;el('chatsend').disabled=false;}
  });
  el('chattext').addEventListener('keydown',event=>{
    if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();el('chatform').requestSubmit();}
  });
})();

/* RCP_COLONY_VIEWER_V1: no generated code runs in the browser. */
(() => {
  'use strict';
  const copy = v => JSON.parse(JSON.stringify(v));
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  function validate(state) {
    if (!state || !finite(state.tick) || !Array.isArray(state.organisms) || !Array.isArray(state.food))
      throw Error('Эта версия не вернула состояние колонии: tick, organisms, food.');
    if (state.organisms.length > 1000 || state.food.length > 3000) throw Error('Предел отображения: 1000 организмов и 3000 точек еды.');
    const ids = new Set();
    for (const item of [...state.organisms, ...state.food]) {
      if (!item || !finite(item.x) || !finite(item.y) || Math.abs(item.x) > 1e6 || Math.abs(item.y) > 1e6)
        throw Error('Некорректные координаты. Предыдущая сцена сохранена.');
    }
    for (const o of state.organisms) {
      if (!['number', 'string'].includes(typeof o.id) || !finite(o.energy)) throw Error('У организма отсутствует id или числовая энергия.');
      const id = JSON.stringify(o.id);
      if (ids.has(id)) throw Error('Повторяющиеся id организмов: нельзя достоверно сравнить кадры.');
      ids.add(id);
    }
    return state;
  }
  function compatible(input) {
    try { validate(input.state); return input.action?.type === 'step'; } catch (_) { return false; }
  }
  function demo(template) {
    const input = copy(template), source = input.state.organisms[0] || {id: 1, x: 0, y: 0, energy: 14, genes: {speed: 1}};
    input.state.tick = 0; input.state.seed = 42; input.state.organisms = []; input.state.food = [];
    for (let i = 0; i < 8; i++) {
      const speed = 1 + i % 3, o = copy(source);
      Object.assign(o, {id: typeof source.id === 'string' ? String(i + 1) : i + 1, x: 2, y: 2 + i * 2, energy: 14 + i % 4 * 2});
      o.genes = {...(o.genes || {}), speed}; input.state.organisms.push(o);
      for (let j = 1; j <= 5; j++) input.state.food.push({x: 2 + j * speed * 3, y: o.y, calories: 5 + j % 2 * 3});
    }
    validate(input.state); return input;
  }
  function stats(s) {
    return {tick: s.tick, count: s.organisms.length, food: s.food.length,
      energy: s.organisms.length ? s.organisms.reduce((n, o) => n + o.energy, 0) / s.organisms.length : 0};
  }
  function difference(before, after) {
    const a = new Set(before.organisms.map(o => JSON.stringify(o.id))), b = new Set(after.organisms.map(o => JSON.stringify(o.id)));
    return {added: [...b].filter(id => !a.has(id)).length, removed: [...a].filter(id => !b.has(id)).length,
      foodRemoved: Math.max(0, before.food.length - after.food.length)};
  }
  // shortcut: this viewer supports the existing colony contract; other projects retain the JSON playground.
  window.RCPColony = {validate, compatible, demo, stats, difference};
  const $ = id => document.getElementById(id);
  const divider = $('codepanel')?.querySelector('hr.divider');
  if (divider && !$('raw-playground')) {
    const raw = document.createElement('details'); raw.id = 'raw-playground'; raw.open = true;
    const heading = document.createElement('summary'); heading.textContent = 'Ручной ввод JSON · отладка'; raw.append(heading);
    divider.before(raw);
    while (divider.nextSibling) raw.append(divider.nextSibling);
    divider.remove();
  }
  const panel = document.createElement('section'); panel.id = 'colony-viewer'; panel.className = 'panel hidden';
  panel.innerHTML = `<style>
#colony-viewer{overflow:hidden}#colony-viewer h2{margin:5px 0 0}.cv-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}.cv-controls select{width:auto;max-width:100%}.cv-controls button{white-space:normal}.cv-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0 14px}.cv-stats strong{display:block;font-size:25px;font-variant-numeric:tabular-nums}.cv-stats span{font-size:12px;color:var(--muted)}#cv-canvas{display:block;width:100%;height:360px;background:#10161a;border:1px solid var(--line);border-radius:9px}#cv-chart{display:block;width:100%;height:80px}.cv-legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin-top:10px}.cv-dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#80d9cf;margin-right:5px}.cv-food{border-radius:2px;background:#e6b96c}#cv-info{min-height:2em}#cv-notice{white-space:pre-wrap;overflow-wrap:anywhere}#cv-events{max-height:100px;overflow:auto;font-size:12px}#cv-events p{margin:4px 0}#cv-raw,#cv-input{max-height:260px;overflow:auto}#cv-organisms{max-height:210px;overflow:auto}#cv-version{max-width:100%;overflow-wrap:anywhere}#cv-new{margin:6px 0}#cv-debug summary{padding:10px 0}@media(max-width:600px){#cv-canvas{height:300px}.cv-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.cv-controls button{flex:1 1 auto}}
</style>
<div class="wide-row"><div><div class="eyebrow">Результат программы</div><h2>Колония · живая сцена</h2></div><span id="cv-version" class="pill"></span></div>
<p class="note">Отдельный прогон принятого кода. Кнопки ниже не останавливают развитие проекта и не меняют его тесты.</p>
<button id="cv-new" class="hidden">Применить новую версию и сбросить сцену</button>
<label for="cv-scene">Начальные условия</label><div class="cv-controls"><select id="cv-scene" aria-label="Начальные условия колонии"></select><button id="cv-reset">Сбросить сцену</button></div>
<p id="cv-scene-note" class="note"></p>
<div class="cv-stats"><div><strong id="cv-tick">0</strong><span>тик симуляции</span></div><div><strong id="cv-population">0</strong><span>организмов</span></div><div><strong id="cv-energy">0</strong><span>средняя энергия</span></div><div><strong id="cv-food">0</strong><span>точек еды</span></div></div>
<canvas id="cv-canvas" role="img" aria-label="Поле колонии: организмы и еда. Числовые данные доступны в таблице ниже."></canvas>
<div class="cv-legend"><span><i class="cv-dot"></i>Организмы · номер = id, ободок = энергия</span><span><i class="cv-dot cv-food"></i>Еда</span></div>
<p id="cv-info" class="note">Нажми на организм, чтобы увидеть его параметры.</p>
<div class="cv-controls"><button id="cv-step" class="primary">1 тик</button><button id="cv-ten">10 тиков</button><button id="cv-play">▶ Автопоказ</button><button id="cv-stop" disabled>Остановить показ</button><label for="cv-speed" class="hidden">Пауза между тиками</label><select id="cv-speed" aria-label="Пауза между тиками"><option value="1000">1 тик / с</option><option value="500">до 2 тиков / с</option><option value="2000">1 тик / 2 с</option></select></div>
<p id="cv-notice" role="status" class="note">Сцена готова. Нажми «1 тик» или «Автопоказ».</p>
<p class="tiny muted">Сетка — масштаб просмотра, а не граница мира. Автопоказ: до 200 тиков за запуск; скрытие вкладки останавливает только показ.</p>
<canvas id="cv-chart" role="img" aria-label="Численность организмов по последним тикам"></canvas>
<div id="cv-events" aria-label="Изменения между состояниями"></div>
<details><summary>Организмы · координаты, энергия и гены</summary><div id="cv-organisms"><table><thead><tr><th>id</th><th>x, y</th><th>Энергия</th><th>Гены</th></tr></thead><tbody id="cv-rows"></tbody></table></div></details>
<details id="cv-debug"><summary>Данные и ручной JSON-ввод</summary><pre id="cv-raw" class="logs"></pre><label for="cv-input">Вход для одного шага</label><textarea id="cv-input" class="mono" rows="5" spellcheck="false"></textarea><button id="cv-load">Показать этот вход</button></details>`;
  const anchor = $('projectstatus') || $('chatpanel');
  if (!anchor) return;
  anchor.after(panel);
  let mission = null, latest = null, version = null, input = null, scenarios = [], samples = [], events = [];
  let request = null, playing = false, epoch = 0, timer = null, busy = false, bounds = null, hitpoints = [];
  let selectedId = null, acceptedElsewhere = null;
  const format = n => Number.isInteger(n) ? String(n) : Number(n).toFixed(2);
  const say = (text, error = false) => { $('cv-notice').textContent = text; $('cv-notice').classList.toggle('warning', error); };
  function buttons() {
    const mismatch = version && latest && (version !== latest.progress.version || !!acceptedElsewhere);
    for (const id of ['cv-step', 'cv-ten', 'cv-play']) $(id).disabled = !input || busy || playing || !!mismatch;
    $('cv-stop').disabled = !busy && !playing;
    $('cv-reset').disabled = busy; $('cv-load').disabled = busy; $('cv-scene').disabled = busy;
    $('cv-new').classList.toggle('hidden', !mismatch); $('cv-new').disabled = busy;
  }
  function stop(text) {
    epoch++; playing = false; clearTimeout(timer); request?.abort();
    if (text) say(text); buttons();
  }
  function setInput(value) {
    validate(value?.state); if (value.action?.type !== 'step') throw Error('Для этой сцены нужен action.type = step.');
    stop(); input = copy(value); version = latest.progress.version; acceptedElsewhere = null;
    selectedId = null; bounds = null; samples = [stats(input.state)]; events = [];
    $('cv-version').textContent = version + ' · принятый код';
    say('Начальное состояние. Оно ещё не выполнено программой.'); render(); buttons();
  }
  function reset() {
    try {
      const index = $('cv-scene').value;
      const value = index === 'demo' ? demo(scenarios[0]) : copy(scenarios[Number(index)]);
      $('cv-scene-note').textContent = index === 'demo'
        ? 'Демо-условия: 8 организмов и 40 точек еды, seed 42. Это заданный старт, а не результат эволюционного поиска.'
        : 'Начальное состояние из сохранённого теста. Дальнейшие тики вычисляет программа.';
      setInput(value);
    } catch (e) { say(e.message, true); }
  }
  function setup(r) {
    stop(); mission = r.config.id; latest = r;
    scenarios = (r.progress.tests || []).map(t => t.input).filter(compatible);
    panel.classList.toggle('hidden', !scenarios.length || !r.progress.code);
    const raw = $('raw-playground'); if (raw) raw.open = !scenarios.length;
    if (!scenarios.length || !r.progress.code) { input = null; return; }
    $('cv-scene').replaceChildren(new Option('Демо-колония · 8 организмов + еда', 'demo'));
    scenarios.forEach((s, i) => $('cv-scene').add(new Option('Пример ' + (i + 1) + ' · организмов: ' + s.state.organisms.length, String(i))));
    reset();
  }
  window.rcpColonyUpdate = r => {
    if (!r?.config || !r.progress) return;
    if (mission !== r.config.id || (!input && r.progress.code)) { setup(r); return; }
    latest = r;
    if (version && r.progress.version !== version) {
      if (playing || busy) stop('Принят новый код. Показ остановлен, чтобы не смешивать версии; примени новую версию кнопкой выше.');
    }
    buttons();
  };
  async function step(token) {
    if (!input || token !== epoch) return false;
    const before = copy(input), expected = version, mid = mission;
    if (new TextEncoder().encode(JSON.stringify(input)).length > 15000) throw Error('Сцена превысила 15 КБ входных данных. Сбрось её или уменьши число объектов.');
    busy = true; const controller = new AbortController(); request = controller; buttons();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/missions/' + mid + '/try', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({input: before}), signal: controller.signal});
      const data = await response.json();
      if (token !== epoch || mid !== mission) return false;
      if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Ошибка выполнения: HTTP ' + response.status);
      if (data.version !== expected) {
        acceptedElsewhere = data.version;
        throw Error('На сервере уже ' + data.version + '. Старый кадр сохранён. Примени новую версию и начни сцену заново.');
      }
      if (data.result?.ok !== true) throw Error(data.result?.error || 'Программа не вернула успешный результат.');
      const state = validate(data.result.value?.state);
      if (state.tick <= before.state.tick) throw Error('Тик не увеличился. Показ остановлен: программа не продвинула симуляцию.');
      const delta = difference(before.state, state);
      input = {...before, state: copy(state)};
      samples.push(stats(state)); samples = samples.slice(-200);
      events.unshift('Тик ' + state.tick + ': появилось ' + delta.added + ', исчезло ' + delta.removed + ', убрано точек еды ' + delta.foodRemoved + '.');
      events = events.slice(0, 20); render();
      say('Тик ' + state.tick + ' выполнен программой ' + data.version + '.');
      return true;
    } finally {
      clearTimeout(timeout); if (request === controller) request = null; busy = false; buttons();
    }
  }
  async function run(count, autoplay) {
    if (busy || playing || !input) return;
    const token = ++epoch; playing = true; buttons();
    for (let i = 0; i < count && token === epoch; i++) {
      try { if (!await step(token)) break; }
      catch (e) { if (token === epoch) say(e.name === 'AbortError' ? 'Программа не ответила за 15 секунд. Предыдущий кадр сохранён.' : e.message, true); break; }
      if (!input.state.organisms.length) { say('В этой сцене больше нет организмов. Развитие проекта продолжается независимо.'); break; }
      if (i + 1 < count && token === epoch) await new Promise(resolve => setTimeout(resolve, autoplay ? Number($('cv-speed').value) : 250));
    }
    if (token === epoch) { playing = false; buttons(); }
  }
  function surface(id, height) {
    const canvas = $(id), w = Math.max(1, canvas.clientWidth), dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); return {ctx, w, h: height};
  }
  function draw() {
    if (!input || panel.classList.contains('hidden')) return;
    const state = input.state, {ctx, w, h} = surface('cv-canvas', $('cv-canvas').clientHeight);
    const objects = [...state.organisms, ...state.food];
    const xs = objects.map(o => o.x), ys = objects.map(o => o.y);
    const b = {left: Math.min(-1, ...xs) - 1, right: Math.max(12, ...xs) + 2, top: Math.min(-1, ...ys) - 1, bottom: Math.max(8, ...ys) + 2};
    if (!bounds) bounds = b;
    else bounds = {left: Math.min(bounds.left, b.left), right: Math.max(bounds.right, b.right), top: Math.min(bounds.top, b.top), bottom: Math.max(bounds.bottom, b.bottom)};
    const scale = Math.min((w - 48) / (bounds.right - bounds.left), (h - 44) / (bounds.bottom - bounds.top));
    const ox = (w - (bounds.right - bounds.left) * scale) / 2, oy = (h - (bounds.bottom - bounds.top) * scale) / 2;
    const x = n => ox + (n - bounds.left) * scale, y = n => oy + (n - bounds.top) * scale;
    ctx.clearRect(0, 0, w, h); ctx.font = '11px system-ui'; ctx.strokeStyle = '#263137'; ctx.fillStyle = '#87979f'; ctx.lineWidth = 1;
    const increment = Math.max(1, Math.ceil(28 / scale));
    for (let n = Math.ceil(bounds.left / increment) * increment; n <= bounds.right; n += increment) { ctx.beginPath(); ctx.moveTo(x(n), 20); ctx.lineTo(x(n), h - 20); ctx.stroke(); ctx.fillText(String(n), x(n) + 3, h - 5); }
    for (let n = Math.ceil(bounds.top / increment) * increment; n <= bounds.bottom; n += increment) { ctx.beginPath(); ctx.moveTo(20, y(n)); ctx.lineTo(w - 15, y(n)); ctx.stroke(); ctx.fillText(String(n), 2, y(n) - 3); }
    ctx.fillStyle = '#e6b96c';
    for (const f of state.food) { const r = Math.max(2, Math.min(4, scale * .15)); ctx.fillRect(x(f.x) - r, y(f.y) - r, r * 2, r * 2); }
    hitpoints = [];
    for (const o of state.organisms) {
      const px = x(o.x), py = y(o.y), radius = Math.max(5, Math.min(11, scale * .33));
      ctx.fillStyle = '#80d9cf'; ctx.beginPath(); ctx.arc(px, py, radius, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = o.id === selectedId ? '#ffffff' : '#b9ed81'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, radius + 3, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0, Math.min(1, o.energy / 30))); ctx.stroke();
      ctx.fillStyle = '#102526'; ctx.textAlign = 'center'; ctx.fillText(String(o.id).slice(0, 5), px, py + 3); ctx.textAlign = 'left';
      hitpoints.push({o, px, py, radius});
    }
    if (!state.organisms.length) { ctx.fillStyle = '#bcc6cb'; ctx.font = '16px system-ui'; ctx.fillText('Организмов нет', 26, 35); }
    const chart = surface('cv-chart', 80), c = chart.ctx, max = Math.max(1, ...samples.map(s => s.count));
    c.clearRect(0, 0, chart.w, 80); c.font = '11px system-ui'; c.fillStyle = '#a6aab7'; c.fillText('Численность по тикам · максимум ' + max, 0, 13);
    c.strokeStyle = '#80d9cf'; c.lineWidth = 2; c.beginPath();
    samples.forEach((s, i) => { const px = 2 + i * (chart.w - 6) / Math.max(1, samples.length - 1), py = 70 - 48 * s.count / max; if (i) c.lineTo(px, py); else c.moveTo(px, py); }); c.stroke();
  }
  function render() {
    const s = stats(input.state); $('cv-tick').textContent = s.tick; $('cv-population').textContent = s.count;
    $('cv-energy').textContent = format(s.energy); $('cv-food').textContent = s.food;
    $('cv-input').value = JSON.stringify(input, null, 2); $('cv-raw').textContent = JSON.stringify({version, state: input.state}, null, 2);
    $('cv-rows').replaceChildren();
    for (const o of input.state.organisms.slice(0, 100)) {
      const tr = document.createElement('tr');
      for (const value of [o.id, format(o.x) + ', ' + format(o.y), format(o.energy), JSON.stringify(o.genes || {})]) { const td = document.createElement('td'); td.textContent = String(value); tr.append(td); }
      $('cv-rows').append(tr);
    }
    $('cv-events').replaceChildren(); for (const event of events.slice(0, 5)) { const p = document.createElement('p'); p.textContent = event; $('cv-events').append(p); }
    draw();
  }
  $('cv-step').onclick = () => run(1, false); $('cv-ten').onclick = () => run(10, false); $('cv-play').onclick = () => run(200, true);
  $('cv-stop').onclick = () => stop('Показ остановлен. Автопилот разработки не затронут.');
  $('cv-reset').onclick = reset; $('cv-scene').onchange = reset;
  $('cv-new').onclick = () => setup(latest);
  $('cv-load').onclick = () => { try { setInput(JSON.parse($('cv-input').value)); $('cv-scene-note').textContent = 'Пользовательские начальные условия.'; } catch (e) { say(e.message, true); } };
  $('cv-canvas').onclick = event => {
    const rect = $('cv-canvas').getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
    const hit = hitpoints.findLast(p => Math.hypot(p.px - px, p.py - py) <= p.radius + 8);
    if (hit) { selectedId = hit.o.id; $('cv-info').textContent = 'Организм ' + hit.o.id + ' · (' + format(hit.o.x) + ', ' + format(hit.o.y) + ') · энергия ' + format(hit.o.energy) + ' · гены ' + JSON.stringify(hit.o.genes || {}); draw(); }
  };
  document.addEventListener('visibilitychange', () => { if (document.hidden && (playing || busy)) stop('Вкладка скрыта — показ остановлен. Развитие проекта продолжается.'); });
  new ResizeObserver(draw).observe($('cv-canvas'));
  if (typeof current !== 'undefined' && current) window.rcpColonyUpdate(current);
})();

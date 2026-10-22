'use strict';
// Run with Node and Playwright installed: node tests/live-status.test.cjs
// Read-only browser regression: every backend response is a fixture, no model needed.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.RCP_PLAYWRIGHT||'playwright');
const htmlPath=process.argv[2]||path.join(__dirname,'../static/autopilot.html');
const html=fs.readFileSync(htmlPath,'utf8');
const A='aaaaaaaaaaaa',B='bbbbbbbbbbbb';
const copy=x=>JSON.parse(JSON.stringify(x));
function mission(id,paused=false){return {
 config:{id,goal:paused?'Сохранённый проект':'Колония цифровых организмов',created:1700000000},
 state:{status:paused?'paused':'running',phase:paused?'paused':'evolving',step:paused?'Сохранено':'Проверяю новое поколение',cycle:3,updated:1700000002,last_error:null},
 control:{desired:paused?'paused':'running'},plan:{title:paused?'Архив':'Колония',contract:'JSON in/out',roadmap:[],assumptions:''},
 progress:{code:'def solve(d): return d',tests:[{input:{},expected:{}}],cycle:2,evaluated:8,code_changes:1,version:'v00000001'},
 attempts:[{time:1700000005,metrics:{passed:1,total:2,code_length:22},failures:[]}],
 history:[{time:1700000001,kind:'code',text:'Сохранена рабочая версия',tests:1}],versions:['v00000001'],log:''};}
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 const results=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage(),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  let active=mission(A),archived=mission(B,true),offline=false;
  let health={model_ready:true,model:'Local model',busy:true,active_mission:A,active_id:null};
  await context.route('http://rcp.test/**',async route=>{
   const req=route.request(),p=new URL(req.url()).pathname;
   if(req.method()!=='GET'){writes.push(p);return route.fulfill({status:500,json:{detail:'Unexpected write'}})}
   if(p==='/')return route.fulfill({contentType:'text/html',body:html});
   if(p==='/chat.js')return route.fulfill({contentType:'application/javascript',body:'window.rcpChat=()=>document.getElementById("chatpanel").classList.remove("hidden");'});
   if(p==='/api/health')return route.fulfill({status:offline?503:200,json:offline?{detail:'Offline'}:health});
   if(p==='/api/missions')return route.fulfill({json:[{id:A,goal:active.config.goal,desired:active.control.desired},{id:B,goal:archived.config.goal,desired:'paused'}]});
   if(p==='/api/missions/'+A)return route.fulfill({json:active});
   if(p==='/api/missions/'+B)return route.fulfill({json:archived});
   return route.fulfill({status:404,json:{detail:'Unknown fixture '+p}});
  });
  const text=id=>page.locator('#'+id).innerText();
  async function poll(){await page.evaluate(()=>refresh());await page.waitForTimeout(100);}
  await page.goto('http://rcp.test/');
  await page.waitForFunction(()=>document.getElementById('live-cycle').textContent==='3');
  assert.equal(await page.locator('#launch').isDisabled(),true);
  assert.match(await text('launch-reason'),/Эксперимент уже идёт/);
  assert.equal(await text('live-evaluated'),'9');
  assert.equal(await text('live-tests'),'1');
  assert.match(await text('live-last'),/1 \/ 2/);
  const beforeChat=await page.evaluate(()=>document.getElementById('projectstatus').compareDocumentPosition(document.getElementById('chatpanel'))&Node.DOCUMENT_POSITION_FOLLOWING);
  assert.ok(beforeChat);results.push('busy reason, candidate counts, status before conversation');
  await page.fill('#goal','Сохранить мой черновик без запуска');
  await page.fill('#chattext','Сообщение, которое ещё не отправлено');
  active.state.cycle=4;active.state.step='Следующий этап';active.progress.cycle=3;
  await page.waitForFunction(()=>document.getElementById('live-cycle').textContent==='4',{},{timeout:6000});
  assert.equal(await page.locator('#goal').inputValue(),'Сохранить мой черновик без запуска');
  assert.equal(await page.locator('#chattext').inputValue(),'Сообщение, которое ещё не отправлено');
  results.push('automatic polling preserves unsubmitted input');
  await page.selectOption('#missionlist',B);
  await page.waitForFunction(()=>document.getElementById('title').textContent==='Архив');
  assert.equal(await text('live-title'),'Колония');
  await page.click('#live-show');
  await page.waitForFunction(()=>document.getElementById('missionlist').value==='aaaaaaaaaaaa');
  assert.equal(new URL(page.url()).searchParams.get('project'),A);
  results.push('active experiment remains visible when browsing a paused project');
  active.progress.cycle=4;active.progress.evaluated=9;
  await poll();assert.equal(await text('live-evaluated'),'9');assert.equal(await text('candidates'),'9');results.push('committed attempts are not counted twice');
  offline=true;await poll();
  assert.equal(await page.locator('#live-status').evaluate(e=>e.classList.contains('stale')),true);
  assert.equal(await page.locator('#launch').isDisabled(),true);
  assert.match(await text('live-phase'),/НЕ ОБНОВЛЯЮТСЯ/);
  offline=false;await poll();assert.equal(await page.locator('#live-status').evaluate(e=>e.classList.contains('stale')),false);
  assert.equal(await page.locator('#message').isVisible(),false);results.push('offline status and automatic recovery');
  health.model_ready=false;active.state.phase='waiting_model';await poll();
  assert.match(await text('live-phase'),/ОЖИДАНИЕ/);assert.equal(await page.locator('#launch').isDisabled(),true);
  health={...health,model_ready:true,busy:false,active_mission:null};active.control.desired='paused';active.state.phase='attention';active.state.last_error='Вход требует уточнения';
  await poll();assert.equal(await page.locator('#launch').isEnabled(),true);assert.match(await text('live-phase'),/ПРОВЕРКА/);assert.equal(await text('live-error'),'Вход требует уточнения');
  results.push('waiting model and paused attention are distinct');
  active.plan.title='<img src=x onerror="window.injected=true">';await poll();
  assert.equal(await page.locator('#live-title img').count(),0);assert.equal(await page.evaluate(()=>!!window.injected),false);
  results.push('project text cannot become executable markup');
  active.plan.title='Колония';await poll();
  for(const width of [390,768,1440]){
   await page.setViewportSize({width,height:900});
   const layout=await page.evaluate(()=>({w:innerWidth,s:document.documentElement.scrollWidth}));
   assert.ok(layout.s<=layout.w+2,JSON.stringify(layout));
  }
  results.push('390/768/1440 layouts do not overflow');
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);results.push('no mutations or JavaScript exceptions');
  console.log(JSON.stringify({passed:results.length,checks:results},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

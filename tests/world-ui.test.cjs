const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage(),errors=[],writes=[];
 page.on('pageerror',e=>errors.push(String(e)));
 const now=Date.now()/1000,mid='aaaaaaaaaaaa';
 let c={frame:50,attempts:3,accepted:2,name:'Тестовый чужой мир',phase:'imagining',seed_removed:true,version:'g000003',
  question:'Может ли память менять взаимодействия?',hypothesis:'Изменение связей',metrics:{activity:1,novelty:.2},
  view:{nodes:[{id:'alpha',x:0,y:0,kind:'echo',value:2},{id:'beta',x:5,y:3,kind:'void',value:4}],links:[{source:'alpha',target:'beta'}],caption:'Фактический кадр'},state:{memory:2},
  history:[{kind:'accepted',time:now,text:'Законы приняты'}],archive:[],code:'def solve(d): pass'};
 let desired='running';
 await page.route('http://rcp.test/**',async route=>{
  const url=new URL(route.request().url()),pathname=url.pathname;
  if(pathname==='/world')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.resolve(__dirname,'../static/world.html'),'utf8')});
  if(route.request().method()!=='GET')writes.push({path:pathname,body:route.request().postDataJSON()});
  let data;
  if(pathname==='/api/health')data={model_ready:true,model:'Local test model',busy:desired==='running',active_world:desired==='running'?mid:null};
  else if(pathname==='/api/worlds')data=[{id:mid,started:now,deadline:now+86400,control:{desired}}];
  else if(pathname==='/api/worlds/'+mid)data={config:{id:mid,started:now,deadline:now+86400},checkpoint:c,control:{desired},server_time:Date.now()/1000};
  else if(pathname.endsWith('/messages'))data=route.request().method()==='GET'?[]:{reply:'Read-only answer'};
  else if(pathname.endsWith('/pause')){desired='paused';data={status:'paused'};}
  else if(pathname.endsWith('/resume')){desired='running';data={status:'running'};}
  else throw Error('Unexpected request '+pathname);
  await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 try{
  await page.goto('http://rcp.test/world?world='+mid,{waitUntil:'networkidle'});
  assert.equal(await page.locator('#ticks').innerText(),'50');
  assert.equal(await page.locator('#nodes').innerText(),'2');
  assert.match(await page.locator('#seedstatus').innerText(),/отсоединена/);
  assert.ok(await page.locator('#create').isDisabled());
  assert.equal(writes.length,0);
  await page.fill('#questioninput','Неотправленный вопрос');c.frame=51;
  await page.waitForTimeout(3000);
  assert.equal(await page.locator('#ticks').innerText(),'51');
  assert.equal(await page.locator('#questioninput').inputValue(),'Неотправленный вопрос');
  assert.match(await page.locator('#timer').innerText(),/^23:/);
  c.name='<img src=x onerror="window.injected=true">';await page.waitForTimeout(2700);
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  assert.equal(await page.locator('#name img').count(),0);
  await page.click('#pause');await page.waitForTimeout(500);
  assert.equal(writes[0].path,'/api/worlds/'+mid+'/pause');
  assert.match(await page.locator('#phase').innerText(),/ПАУЗЕ/);
  for(const width of [390,768,1440]){
   await page.setViewportSize({width,height:900});await page.waitForTimeout(150);
   const size=await page.evaluate(()=>({w:innerWidth,s:document.documentElement.scrollWidth}));
   assert.ok(size.s<=size.w+2,JSON.stringify(size));
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:8,checks:['Server frame, no client invented dynamics','Seed detachment visible','No automatic experiment mutations','Live polling preserves draft','Absolute deadline countdown','Text injection inert','Explicit pause only','390/768/1440 layout and JS checks']},null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});

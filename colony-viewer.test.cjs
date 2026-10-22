// Browser tests use a synthetic colony; no real service or model is contacted.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=process.env.RCP_UI_DIR||path.join(__dirname,'static');
const html=fs.readFileSync(path.join(root,'autopilot.html'),'utf8'),script=fs.readFileSync(path.join(root,'chat.js'),'utf8');
const input={state:{tick:0,seed:42,organisms:[{id:1,x:0,y:0,energy:10,genes:{speed:1}}],food:[{x:1,y:0,calories:5}]},action:{type:'step'}};
const other={...input,state:{...input.state,organisms:[{id:2,x:0,y:1,energy:1,genes:{speed:1}}],food:[]}};
const clone=v=>JSON.parse(JSON.stringify(v));
let modelVersion='v00000001',fail='',delay=0;const posts=[],errors=[],passed=[];
const mission={config:{id:'aaaaaaaaaaaa',goal:'Synthetic colony',created:1},control:{desired:'running'},state:{status:'running',phase:'evolving',cycle:4},plan:{title:'Colony',roadmap:[],contract:'state and action',assumptions:''},progress:{version:modelVersion,code:'def solve(d): return d',tests:[{input,expected:{}},{input:other,expected:{}}],cycle:3,evaluated:9,code_changes:1},attempts:[],history:[],versions:[modelVersion],log:''};
const plain=clone(mission);plain.config.id='bbbbbbbbbbbb';plain.config.goal='Numbers';plain.control.desired='paused';plain.progress.tests=[{input:{numbers:[1]},expected:{sum:1}}];
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}});page.on('pageerror',e=>errors.push(String(e)));
  await page.route('http://rcp.test/**',async route=>{
   const request=route.request(),url=new URL(request.url()),method=request.method();
   if(method==='POST')posts.push(url.pathname);
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(url.pathname==='/chat.js')return route.fulfill({contentType:'application/javascript',body:script});
   let value;
   if(url.pathname==='/api/health')value={model_ready:true,busy:true,active_mission:mission.config.id,model:'Local test model'};
   else if(url.pathname==='/api/missions')value=[mission,plain].map(m=>({...m.config,...m.state,desired:m.control.desired}));
   else if(url.pathname.endsWith('/messages'))value={messages:[],guidance:null,priority_status:'none'};
   else if(url.pathname.endsWith('/try')){
    assert.equal(method,'POST');if(delay)await new Promise(r=>setTimeout(r,delay));
    if(fail==='http')return route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'Sandbox timeout'})});
    const s=clone(request.postDataJSON().input.state);s.tick++;
    for(const o of s.organisms){o.energy--;o.x+=o.genes.speed;const f=s.food.find(f=>f.x===o.x&&f.y===o.y);if(f){o.energy+=f.calories;s.food.splice(s.food.indexOf(f),1);}}
    s.organisms=s.organisms.filter(o=>o.energy>0);
    if(fail==='shape')s.organisms[0].x='invalid';
    if(fail==='tick')s.tick--;
    value={version:modelVersion,result:fail==='solver'?{ok:false,error:'Actual solver error'}:{ok:true,value:{state:s}}};
   }else if(url.pathname==='/api/missions/'+mission.config.id)value={...mission,progress:{...mission.progress,version:modelVersion}};
   else if(url.pathname==='/api/missions/'+plain.config.id)value=plain;
   else return route.fulfill({status:404,body:'{}'});
   return route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  });
  await page.goto('http://rcp.test/');await page.waitForSelector('#colony-viewer:not(.hidden)');
  const text=id=>page.locator('#'+id).innerText();
  const tick=()=>text('cv-tick');
  async function step(){await page.click('#cv-step');await page.waitForFunction(()=>!document.getElementById('cv-stop').disabled);await page.waitForFunction(()=>document.getElementById('cv-stop').disabled);}
  assert.equal(posts.length,0);assert.equal(await text('cv-population'),'8');assert.equal(await text('cv-food'),'40');
  assert.equal(await page.locator('#raw-playground').getAttribute('open'),null);
  assert.ok(await page.locator('#colony-viewer').evaluate(n=>n.compareDocumentPosition(document.getElementById('chatpanel'))&Node.DOCUMENT_POSITION_FOLLOWING));
  passed.push('Visual scene is default; debug JSON collapsed; no automatic execution');
  const points=await page.locator('#cv-canvas').evaluate(c=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(d[i+3]>0)n++;return n;});assert.ok(points>1000);passed.push('Canvas contains grid, organisms and food');
  await page.selectOption('#cv-scene','0');await page.click('#cv-step');
  await page.waitForFunction(()=>document.getElementById('cv-tick').textContent==='1');
  assert.equal(await text('cv-energy'),'14');assert.equal(await text('cv-food'),'0');assert.ok((await text('cv-events')).includes('убрано точек еды 1'));
  const actual=JSON.parse(await page.locator('#cv-raw').textContent());assert.equal(actual.state.organisms[0].x,1);passed.push('Step renders actual server output and energy/food counters');
  await page.fill('#chattext','Draft message');await page.waitForTimeout(2900);assert.equal(await tick(),'1');assert.equal(await page.locator('#chattext').inputValue(),'Draft message');passed.push('Polling does not reset scene or chat draft');
  fail='solver';await page.click('#cv-step');await page.waitForFunction(()=>document.getElementById('cv-notice').textContent.includes('Actual solver error'));assert.equal(await tick(),'1');
  fail='shape';await page.click('#cv-step');await page.waitForFunction(()=>document.getElementById('cv-notice').textContent.includes('Некорректные координаты'));assert.equal(await tick(),'1');
  fail='tick';await page.click('#cv-step');await page.waitForFunction(()=>document.getElementById('cv-notice').textContent.includes('Тик не увеличился'));assert.equal(await tick(),'1');fail='';passed.push('Errors, invalid coordinates and stalled ticks preserve prior frame');
  modelVersion='v00000002';await page.waitForSelector('#cv-new:not(.hidden)');assert.ok(await page.locator('#cv-step').isDisabled());assert.equal(await tick(),'1');
  await page.click('#cv-new');assert.equal(await tick(),'0');assert.ok((await text('cv-version')).startsWith(modelVersion));passed.push('New accepted version requires explicit reset');
  await page.selectOption('#cv-scene','1');await page.click('#cv-play');await page.waitForFunction(()=>document.getElementById('cv-population').textContent==='0');assert.ok(await page.locator('#cv-stop').isDisabled());passed.push('Extinction stops playback, not development');
  await page.selectOption('#cv-scene','0');delay=500;await page.click('#cv-play');await page.waitForFunction(()=>!document.getElementById('cv-stop').disabled);await page.click('#cv-stop');await page.waitForTimeout(700);assert.equal(await tick(),'0');delay=0;passed.push('Stopping ignores in-flight result');
  await page.click('#cv-ten');await page.waitForFunction(()=>document.getElementById('cv-tick').textContent==='10',{},{timeout:12000});assert.ok(await page.locator('#cv-stop').isDisabled());passed.push('Ten ticks execute sequentially');
  const helper=await page.evaluate(()=>{
   const C=window.RCPColony,s={tick:0,organisms:[{id:1,x:0,y:0,energy:4}],food:[]};let rejects=0;
   for(const bad of [{}, {...s,organisms:[...s.organisms,...s.organisms]}, {...s,food:Array(3001).fill({x:0,y:0})}, {...s,organisms:[{id:1,x:Infinity,y:0,energy:1}]}])try{C.validate(bad);}catch(e){rejects++;}
   return {rejects,delta:C.difference(s,{tick:1,organisms:[],food:[]})};
  });assert.equal(helper.rejects,4);assert.equal(helper.delta.removed,1);passed.push('Shape, size, finite-coordinate and ID validation');
  for(const width of [390,768,1440]){await page.setViewportSize({width,height:1100});await page.waitForTimeout(120);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));}passed.push('Responsive 390/768/1440 widths');
  await page.selectOption('#missionlist',plain.config.id);await page.waitForFunction(()=>document.getElementById('colony-viewer').classList.contains('hidden'));assert.notEqual(await page.locator('#raw-playground').getAttribute('open'),null);passed.push('Unrelated projects retain manual playground');
  assert.deepEqual(errors,[]);assert.ok(posts.every(p=>p==='/api/missions/'+mission.config.id+'/try'));passed.push('No JavaScript errors or experiment control writes');
  console.log(JSON.stringify({passed:passed.length,checks:passed},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

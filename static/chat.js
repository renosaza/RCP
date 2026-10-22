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

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup() {
  const document=new EventTarget(), window=new EventTarget(), canvas=new EventTarget(), calls=[];
  canvas.width=400; canvas.height=800;
  canvas.focus=()=>{};
  canvas.getContext=()=>({});
  canvas.getBoundingClientRect=()=>({left:0,top:0,width:400,height:800});
  document.body=new EventTarget();
  document.body.setPointerCapture=()=>{};
  document.getElementById=id=>id==='c'?canvas:{};
  const fetch=async(url,request)=>{calls.push({url,body:JSON.parse(request.body)});return {ok:true};};
  const source=fs.readFileSync('handset/web/mirror.js','utf8').split('window.HandsetKeys.attach(')[0];
  vm.runInNewContext(source,{document,window,fetch,performance,setTimeout,clearTimeout});
  return {document,window,calls};
}
function emit(target,type,properties){const event=new Event(type,{cancelable:true});Object.assign(event,properties);target.dispatchEvent(event);}
test('touch long-press context event must not send Home or cancel the iOS touch',async()=>{
  const {document,window,calls}=setup();
  emit(document,'pointerdown',{button:0,pointerId:7,pointerType:'touch',clientX:100,clientY:200});
  emit(document,'contextmenu',{button:2,pointerId:7,pointerType:'touch'});
  emit(window,'pointerup',{button:0,pointerId:7,pointerType:'touch',clientX:100,clientY:200});
  await new Promise(r=>setTimeout(r,5));
  assert.equal(calls.some(c=>c.url==='/mirror/button'),false);
  assert.deepEqual(calls.filter(c=>c.url==='/mirror/touch').map(c=>c.body.type),['contact','release']);
});
test('an explicit mouse right-click keeps the documented Home action',async()=>{
  const {document,calls}=setup();
  emit(document,'contextmenu',{button:2,pointerType:'mouse'});
  await new Promise(r=>setTimeout(r,5));
  assert.equal(calls.some(c=>c.body.name==='home'),true);
});

test('a long-press event while the left mouse button is held does not go Home',async()=>{
  const {document,window,calls}=setup();
  emit(document,'pointerdown',{button:0,pointerId:8,pointerType:'mouse',clientX:100,clientY:200});
  emit(document,'contextmenu',{button:2,pointerType:'mouse'});
  emit(window,'pointerup',{button:0,pointerId:8,pointerType:'mouse',clientX:100,clientY:200});
  await new Promise(r=>setTimeout(r,5));
  assert.equal(calls.some(c=>c.url==='/mirror/button'),false);
});

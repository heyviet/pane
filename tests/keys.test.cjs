const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup(options={}) {
  const window=new EventTarget(), document=new EventTarget(), sent=[], errors=[];
  const fetch=async(url,request)=>{
    sent.push({url,body:JSON.parse(request.body)});
    return {ok:options.ok!==false,json:async()=>({message:'Completed',error:'Clipboard unavailable'})};
  };
  vm.runInNewContext(fs.readFileSync('handset/web/keys.js','utf8'),{window,document,fetch});
  const keyboard=window.HandsetKeys.attach({post:async p=>sent.push({key:Array.from(p.usages)}),enabled:()=>options.enabled!==false,onError:e=>errors.push(e.message)});
  return {window,document,sent,errors,keyboard};
}
function emit(window,type,properties){const e=new Event(type,{cancelable:true});Object.assign(e,properties);window.dispatchEvent(e);return e;}
const settle=()=>new Promise(r=>setTimeout(r,5));
test('Ctrl+C and Ctrl+V use the host bridge without browser clipboard permission',async()=>{
  const {window,sent}=setup();
  for(const code of ['KeyC','KeyV'])assert.equal(emit(window,'keydown',{code,ctrlKey:true}).defaultPrevented,true);
  await settle();
  assert.deepEqual(sent,[{url:'/api/clipboard/action',body:{action:'copy'}},{url:'/api/clipboard/action',body:{action:'paste'}}]);
});
test('disabled forwarding leaves shortcuts in the browser',async()=>{
  const {window,sent}=setup({enabled:false});
  assert.equal(emit(window,'keydown',{code:'KeyV',ctrlKey:true}).defaultPrevented,false);
  await settle();assert.equal(sent.length,0);
});
test('ordinary letters retain ordered HID down and up reports',async()=>{
  const {window,sent}=setup();emit(window,'keydown',{code:'KeyA'});emit(window,'keyup',{code:'KeyA'});
  await settle();assert.deepEqual(sent,[{key:[4]},{key:[]}]);
});
test('clipboard failures are reported and never fall through to raw paste',async()=>{
  const {window,sent,errors}=setup({ok:false});emit(window,'keydown',{code:'KeyV',ctrlKey:true});
  await settle();assert.equal(sent.length,1);assert.deepEqual(errors,['Clipboard unavailable']);
});
test('buttons can get and paste the iPhone clipboard separately',async()=>{
  const {keyboard,sent}=setup();await keyboard.action('get');await keyboard.action('paste-device');
  assert.deepEqual(sent.map(r=>r.body.action),['get','paste-device']);
});

test('moving focus inside the window releases a held Space key',async()=>{
  const {window,document,sent}=setup();
  emit(window,'keydown',{code:'Space'});
  document.dispatchEvent(new Event('focusout'));
  await settle();
  assert.deepEqual(sent,[{key:[0x2c]},{key:[]}]);
});

test('Space activates a focused Pane button instead of typing on iPhone',async()=>{
  const {window,sent}=setup();
  const event=new Event('keydown',{cancelable:true});
  Object.defineProperty(event,'target',{value:{tagName:'BUTTON'}});
  Object.assign(event,{code:'Space'});
  window.dispatchEvent(event);
  await settle();
  assert.equal(event.defaultPrevented,false);
  assert.deepEqual(sent,[]);
});

test('paste clears held keys before the clipboard operation begins',async()=>{
  const {window,sent}=setup();
  emit(window,'keydown',{code:'Space'});
  emit(window,'keydown',{code:'KeyV',ctrlKey:true});
  await settle();
  assert.deepEqual(sent,[{key:[0x2c]},{key:[]},{url:'/api/clipboard/action',body:{action:'paste'}}]);
});

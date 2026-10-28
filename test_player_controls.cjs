// Transport UI checks with simulated playback; never controls a real device.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname,'dashboard/app.js'),'utf8');
const elements = new Map();
function $(key) {
  if (!elements.has(key)) elements.set(key,{value:'test-device',textContent:'',disabled:false,style:{},attrs:{},listeners:{},classList:{toggle(){}},
    setAttribute(k,v){this.attrs[k]=v;},querySelector(){return $('toggle-icon');},addEventListener(k,fn){this.listeners[k]=fn;}});
  return elements.get(key);
}
const calls=[], errors=[];
const context = {$,state:{spotify:{connected:true,playback_ready:true},current:{is_playing:false},musicCommandLoading:false},
  timeLabel:()=>'', window:{},
  post:async(path,body)=>calls.push({path,body}),toast:text=>errors.push(text),loadMusic:async()=>{},setTimeout:fn=>fn(),
  deviceBody:()=>({device_id:$('#device-select').value})};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderPlayback()'),source.indexOf('function renderDevices(')),context);
vm.runInContext(source.slice(source.indexOf('function setMusicControls()'),source.indexOf("$('#play-playlist').addEventListener")),context);
(async()=>{
  context.renderPlayback();
  assert.equal($('#play-pause').attrs['aria-label'],'Play Spotify');
  assert.equal($('toggle-icon').attrs.href,'#i-play');
  await $('#play-pause').listeners.click();
  assert.equal(calls[0].path,'/spotify/play');
  assert.equal(calls[0].body.device_id,'test-device');
  assert.equal($('#play-pause').attrs['aria-label'],'Pause Spotify');
  assert.equal($('toggle-icon').attrs.href,'#i-pause');
  await $('#play-pause').listeners.click();
  assert.equal(calls.at(-1).path,'/spotify/pause');
  await $('#previous-track').listeners.click();
  assert.equal(calls.at(-1).path,'/spotify/previous');
  await $('#next-track').listeners.click();
  assert.equal(calls.at(-1).path,'/spotify/next');
  let finish;
  context.post=()=>new Promise(resolve=>{finish=resolve;});
  const pending=$('#next-track').listeners.click();
  assert.equal($('#play-pause').disabled,true);
  assert.equal($('#previous-track').disabled,true);
  await $('#previous-track').listeners.click();
  finish(); await pending;
  assert.equal($('#play-pause').disabled,false);
  context.post=async()=>{throw new Error('Device unavailable');};
  await $('#next-track').listeners.click();
  assert.equal(errors.at(-1),'Device unavailable');
  assert.equal(context.state.musicCommandLoading,false);
  console.log('Transport checks passed: toggle icon/state, device targeting, previous/next, in-flight lock, and error recovery.');
})().catch(error=>{console.error(error);process.exitCode=1;});

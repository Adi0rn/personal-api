// Dashboard controls use fake Spotify responses; no actual playback.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'dashboard/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'dashboard/index.html'),'utf8');
assert(!html.includes('id="track-progress"'));
assert(!html.includes('class="progress-track"'));
const elements=new Map(),calls=[],errors=[],timers=new Map();let timerId=0;
function $(key){
  assert(!/track-progress|progress-track|track-time|track-duration/.test(key),'Removed progress UI must never be accessed');
  if(!elements.has(key))elements.set(key,{value:'test-device',textContent:'',disabled:false,style:{},attrs:{},listeners:{},classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k]??null;},removeAttribute(k){delete this.attrs[k];},querySelector(){return $('toggle-icon');},addEventListener(k,fn){this.listeners[k]=fn;}});
  return elements.get(key);
}
const state={spotify:{connected:true,playback_ready:true},current:{is_playing:false},musicCommandLoading:false,musicRevision:0,musicSettling:false,playlists:[{}]};
const normalPost=async(path,body)=>calls.push({path,body});
const context={document:{hidden:false},AbortSignal,$ ,state,post:normalPost,toast:x=>errors.push(x),deviceBody:()=>({device_id:'test-device'}),
  api:async()=>state.current,renderDevices:()=>{},loadPlaylists:async()=>{},
  setTimeout:(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;},clearTimeout:id=>timers.delete(id)};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderPlayback()'),source.indexOf('function renderDevices(')),context);
vm.runInContext(source.slice(source.indexOf('async function loadMusic('),source.indexOf('function deviceBody(')),context);
vm.runInContext(source.slice(source.indexOf('function setMusicControls()'),source.indexOf("$('#more-playlists').addEventListener")),context);
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function main(){
  const cover=$('#album-cover'),art='https://i.scdn.co/image/test-cover';
  context.renderArtwork({image_url:art});
  assert.equal(cover.hidden,true,'Vinyl stays visible until the artwork loads');
  assert.equal(cover.getAttribute('src'),art);
  cover.naturalWidth=640;cover.onload();
  assert.equal(cover.hidden,false,'Loaded artwork replaces the vinyl');
  cover.onerror();assert.equal(cover.hidden,true,'A failed image falls back to the vinyl');
  context.renderArtwork(null);assert.equal(cover.getAttribute('src'),null);
  context.renderArtwork({image_url:'https://untrusted.example/cover'});
  assert.equal(cover.getAttribute('src'),null,'Artwork only loads from Spotify image hosts');
  context.renderPlayback();assert.equal($('toggle-icon').attrs.href,'#i-play');
  const ack=deferred();context.post=(path,body)=>{calls.push({path,body});return ack.promise;};
  const playing=$('#play-pause').listeners.click();
  assert.equal($('toggle-icon').attrs.href,'#i-pause','Icon changes before network acknowledgement');
  assert.equal($('#play-pause').disabled,true);
  assert.equal(calls.at(-1).body.device_id,'test-device');
  ack.resolve();await playing;
  assert.equal($('#play-pause').disabled,false,'Controls unlock without waiting for a playback/device refresh');
  assert.equal(timers.size,1);
  context.post=normalPost;
  await $('#play-pause').listeners.click();assert.equal(calls.at(-1).path,'/spotify/pause');
  assert.equal($('toggle-icon').attrs.href,'#i-play');
  assert.equal(timers.size,1,'Rapid commands coalesce into one background refresh');

  const oldRead=deferred();context.api=()=>oldRead.promise;
  const timer=[...timers.entries()][0];timers.delete(timer[0]);const refresh=timer[1].callback();
  await $('#play-pause').listeners.click();
  oldRead.resolve({is_playing:false});await refresh;
  assert.equal(state.current.is_playing,true,'An old background read cannot undo a newer play');

  context.post=async()=>{throw new Error('Device unavailable');};
  await $('#play-pause').listeners.click();
  assert.equal(state.current.is_playing,true,'Failure rolls the optimistic toggle back');
  assert.equal($('toggle-icon').attrs.href,'#i-pause');assert.equal(errors.at(-1),'Device unavailable');
  assert.equal($('#play-pause').disabled,false);

  const nextAck=deferred();context.post=(path,body)=>{calls.push({path,body});return nextAck.promise;};
  const count=calls.length;const next=$('#next-track').listeners.click();
  await $('#previous-track').listeners.click();assert.equal(calls.length,count+1,'Commands are serialized while in flight');
  nextAck.resolve();await next;assert.equal(calls.at(-1).path,'/spotify/next');
  context.post=normalPost;await $('#previous-track').listeners.click();assert.equal(calls.at(-1).path,'/spotify/previous');
  $('#playlist-select').value='playlist-id';await $('#play-playlist').listeners.click();
  assert.equal(calls.at(-1).body.playlist,'playlist-id');assert.equal(calls.at(-1).body.device_id,'test-device');

  state.musicSettling=false;const pollRead=deferred();
  context.api=async route=>route==='/spotify/status'?{connected:true,playback_ready:true}:route==='/spotify/currently-playing'?pollRead.promise:{devices:[]};
  const polling=context.loadMusic();await Promise.resolve();await Promise.resolve();
  state.current={is_playing:true};await $('#play-pause').listeners.click();
  pollRead.resolve({is_playing:true});await polling;
  assert.equal(state.current.is_playing,false,'A poll started before the pause cannot overwrite it');
  console.log('Transport checks passed: immediate feedback, acknowledgement-only lock, rollback, stale-read protection, device targeting, playlists, and next/previous.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

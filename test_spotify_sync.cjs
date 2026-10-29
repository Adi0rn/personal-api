// Simulate external Spotify changes; never send playback commands.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const app=fs.readFileSync(path.join(__dirname,'dashboard/app.js'),'utf8');
const sync=fs.readFileSync(path.join(__dirname,'dashboard/spotify-sync.js'),'utf8');
const state={spotify:{playback_ready:true},musicRevision:0,musicLoading:false,musicCommandLoading:false,musicSettling:false};
const timers=new Map(),events={},frames=[];let id=0,calls=0,response={is_playing:true,item:{name:'First song',image_url:'cover-one'}};
const document={hidden:false,addEventListener:(name,fn)=>events[name]=fn};
const context={state,document,AbortSignal,window:{addEventListener:(name,fn)=>events[name]=fn},
  api:async()=>{calls++;return response;},renderPlayback:()=>frames.push(state.current),
  setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},clearTimeout:key=>timers.delete(key)};
vm.createContext(context);
vm.runInContext(app.slice(app.indexOf('async function readPlayback('),app.indexOf('function deviceBody(')),context);
vm.runInContext(sync,context);
async function tick(){assert.equal(timers.size,1);const [key,timer]=timers.entries().next().value;timers.delete(key);await timer.fn();}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
async function main(){
  assert.equal([...timers.values()][0].delay,3000);
  await tick();assert.equal(frames.at(-1).item.name,'First song');
  response={is_playing:true,item:{name:'Next external song',image_url:'cover-two'}};
  await tick();assert.equal(frames.at(-1).item.image_url,'cover-two','External track/artwork updates without page reload');
  response={...response,is_playing:false};await tick();assert.equal(frames.at(-1).is_playing,false);
  state.musicCommandLoading=true;const before=calls;await tick();assert.equal(calls,before);state.musicCommandLoading=false;
  document.hidden=true;events.visibilitychange();assert.equal(timers.size,0);
  document.hidden=false;await events.focus();assert.equal(calls,before+1,'Returning to the page reads Spotify immediately');
  const pending=deferred();context.api=async()=>{calls++;return pending.promise;};
  const reading=events.focus();const inFlight=calls;await events.focus();assert.equal(calls,inFlight,'Focus cannot overlap requests');
  state.musicRevision++;const last=state.current;pending.resolve({item:{name:'Stale'}});await reading;assert.equal(state.current,last);
  const old=deferred(),fresh=deferred();let count=0;
  context.api=()=>++count===1?old.promise:fresh.promise;
  const earlier=context.readPlayback(),later=context.readPlayback();
  fresh.resolve({item:{name:'Newer response'}});await later;old.resolve({item:{name:'Old response'}});await earlier;
  assert.equal(state.current.item.name,'Newer response','An older full refresh cannot overwrite newer live data');
  context.api=async()=>{throw Error('Offline');};await tick();assert.equal([...timers.values()][0].delay,6000);
  assert.equal(state.current.item.name,'Newer response','Network failure preserves the last known track');
  context.api=async()=>({is_playing:false,item:null});await events.online();assert.equal(state.current.item,null);assert.equal([...timers.values()][0].delay,3000);
  console.log('Spotify sync passed: external song/artwork/pause updates, focus refresh, hidden-page pause, non-overlap, stale-response protection, and retry recovery.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

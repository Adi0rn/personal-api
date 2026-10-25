// Simulated SDK lifecycle: no browser credentials, requests, or audio.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, 'dashboard/spotify-browser.js'), 'utf8');
async function fixture(authorized, voice = false, remembered = false) {
  const elements = new Map();
  for (const id of ['browser-enable','browser-play','browser-disconnect','browser-status','browser-reconnect','device-select']) {
    elements.set('#'+id, {disabled:false, hidden:false, textContent:'', value:'', options:[], listeners:{},
      addEventListener(event, fn) { this.listeners[event] = fn; },
      add(option) { this.options.push(option); option.remove = () => { this.options = this.options.filter(x => x !== option); }; }});
  }
  const calls = [], scripts = [], players = [];
  const saved = new Map(remembered ? [['papi-browser-player-enabled','1']] : []);
  const context = { localStorage:{getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)},
    document:{querySelector:id => elements.get(id), head:{append:script => scripts.push(script)}, createElement:() => ({remove(){}})},
    window:{addEventListener(){}, location:{search:voice?'?voice_music=1':''}},
    Option: function(text, value) { this.text = text; this.value = value; },
    Spotify:{Player:class {
      constructor(config) { this.config=config; this.listeners={}; players.push(this); }
      addListener(event, fn) { this.listeners[event]=fn; }
      async connect() { this.config.getOAuthToken(token => calls.push(['token', token])); this.listeners.ready({device_id:'browser-device'}); return true; }
      disconnect() { calls.push(['disconnect']); }
      activateElement() { calls.push(['activate']); return Promise.resolve(); }
    }},
    api:async (url, options) => { calls.push(['api',url,options]); return url.endsWith('/status') ? {browser_playback_ready:authorized} : {access_token:'fake-token'}; },
    post:async (url, body) => calls.push(['post',url,body]),
    busy:async (button, action) => action(), state:{}, renderPlayback(){}, loadMusic:async()=>{},
    URLSearchParams, setInterval:()=>1, clearInterval:()=>{}, setTimeout,clearTimeout
  };
  context.window.Spotify=context.Spotify;
  vm.runInNewContext(code,context);
  await new Promise(resolve => setImmediate(resolve));
  return {elements,calls,scripts,players,context};
}
(async()=>{
  const blocked=await fixture(false);
  await blocked.elements.get('#browser-enable').listeners.click();
  assert.equal(blocked.players.length,0);
  assert.equal(blocked.scripts.length,0);
  assert.equal(blocked.elements.get('#browser-reconnect').hidden,false);
  const f=await fixture(true);
  assert.equal(f.players.length,0,'Player must not connect on page load');
  await f.elements.get('#browser-enable').listeners.click();
  assert.equal(f.players.length,1);
  assert.equal(f.calls.filter(x=>x[0]==='post').length,0,'Enabling must not transfer playback');
  assert.equal(f.elements.get('#device-select').value,'browser-device');
  const token=f.calls.find(x=>x[0]==='api'&&x[1].endsWith('/browser-token'));
  assert.equal(token[2].method,'POST');
  assert.equal(token[2].headers['X-PAPI-Player'],'1');
  await f.elements.get('#browser-play').listeners.click();
  assert.ok(f.calls.findIndex(x=>x[0]==='activate') < f.calls.findIndex(x=>x[0]==='post'));
  assert.equal(f.calls.find(x=>x[0]==='post')[2].play,true);
  f.calls.length=0;
  await f.context.window.papiBrowserPlayer.preparePlayback();
  assert.equal(f.calls[0][0],'activate');
  assert.equal(f.calls[1][2].play,false);
  f.players[0].listeners.autoplay_failed();
  assert.match(f.elements.get('#browser-status').textContent,/Click Play here/);
  f.players[0].listeners.account_error();
  assert.match(f.elements.get('#browser-status').textContent,/Premium/);
  assert.equal(f.elements.get('#browser-enable').disabled,false);
  assert.equal(f.elements.get('#browser-play').disabled,true);
  assert.ok(f.calls.some(x=>x[0]==='disconnect'));
  const rememberedPlayer=await fixture(true,false,true);
  assert.equal(rememberedPlayer.players.length,1,'A previously enabled player should reconnect without another setup click');
  assert.equal(rememberedPlayer.calls.filter(x=>x[0]==='post').length,0,'Remembering the player must not start music');
  await rememberedPlayer.elements.get('#browser-enable').listeners.click();
  assert.ok(rememberedPlayer.calls.some(x=>x[0]==='activate'),'An existing player can unlock without disconnecting');
  rememberedPlayer.elements.get('#browser-disconnect').listeners.click();
  assert.equal(rememberedPlayer.context.localStorage.getItem('papi-browser-player-enabled'),undefined);
  const voice=await fixture(true,true);
  assert.equal(voice.players.length,1,'Voice music links should connect the browser player automatically');
  const registration=voice.calls.find(x=>x[0]==='api'&&x[1]==='/spotify/dashboard-player'&&x[2].method==='POST');
  assert.ok(registration,'The ready browser must publish its device ID');
  assert.equal(JSON.parse(registration[2].body).device_id,'browser-device');
  voice.players[0].listeners.autoplay_failed();
  const reports=voice.calls.filter(x=>x[0]==='api'&&x[1]==='/spotify/dashboard-player'&&x[2].method==='POST');
  assert.equal(JSON.parse(reports.at(-1)[2].body).autoplay_blocked,true);
  voice.players[0].listeners.player_state_changed({paused:false,position:0,track_window:{current_track:{name:'Test',artists:[],duration_ms:1000}}});
  const cleared=voice.calls.filter(x=>x[0]==='api'&&x[1]==='/spotify/dashboard-player'&&x[2].method==='POST');
  assert.equal(JSON.parse(cleared.at(-1)[2].body).autoplay_blocked,false,'Successful SDK playback clears a stale autoplay restriction');

  console.log('Browser lifecycle checks passed: consent, explicit start, token delivery, activation order, transfer, and error cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});

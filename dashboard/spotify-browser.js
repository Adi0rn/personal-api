'use strict';
(() => {
  const enable = document.querySelector('#browser-enable');
  const here = document.querySelector('#browser-play');
  const stop = document.querySelector('#browser-disconnect');
  const message = document.querySelector('#browser-status');
  const reconnect = document.querySelector('#browser-reconnect');
  let player = null, deviceId = null, sdkPromise = null, readyTimer = null;
  let heartbeat = null, autoplayBlocked = false;
  const rememberKey = 'papi-browser-player-enabled';
  function remember(enabled) { try { if (enabled) localStorage.setItem(rememberKey, '1'); else localStorage.removeItem(rememberKey); } catch {} }
  function remembered() { try { return localStorage.getItem(rememberKey) === '1'; } catch { return false; } }
  function unlockAudio() {
    if (!player) return;
    return player.activateElement().then(() => { autoplayBlocked=false; publish(); }).catch(() => {});
  }
  // An ordinary dashboard interaction also unlocks the already-connected player.
  document.addEventListener?.('pointerdown', unlockAudio, {capture:true});
  const voiceStart = new URLSearchParams(window.location?.search || '').get('voice_music') === '1';
  function publish() {
    if (!deviceId) return;
    return api('/spotify/dashboard-player', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({device_id:deviceId,autoplay_blocked:autoplayBlocked})}).catch(() => {});
  }

  function cleanup() {
    clearTimeout(readyTimer);
    clearInterval(heartbeat); heartbeat = null;
    const oldId = deviceId;
    if (oldId) api('/spotify/dashboard-player', {method:'DELETE', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({device_id:oldId}), keepalive:true}).catch(() => {});
    deviceId = null;
    const oldPlayer = player; player = null;
    if (oldPlayer) oldPlayer.disconnect();
    const select = document.querySelector('#device-select');
    for (const option of [...select.options]) if (option.value === oldId && oldId) option.remove();
    here.disabled = true; stop.disabled = true; enable.disabled = false;
  }
  function fail(text, needsAuth = false) {
    cleanup(); message.textContent = text; reconnect.hidden = !needsAuth;
  }
  function loadSDK() {
    if (window.Spotify?.Player) return Promise.resolve();
    if (sdkPromise) return sdkPromise;
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const timer = setTimeout(() => failed(), 30000);
      function failed() { clearTimeout(timer); script.remove(); sdkPromise = null; reject(new Error('Could not load Spotify. Check your connection or content blocker, then try again.')); }
      window.onSpotifyWebPlaybackSDKReady = () => { clearTimeout(timer); resolve(); };
      script.src = 'https://sdk.scdn.co/spotify-player.js';
      script.onerror = failed;
      document.head.append(script);
    });
    return sdkPromise;
  }
  function addDeviceOption(select) {
    if (deviceId && ![...select.options].some(x => x.value === deviceId)) select.add(new Option('PAPI · This browser', deviceId));
  }
  async function preparePlayback() {
    if (!deviceId || document.querySelector('#device-select').value !== deviceId) return;
    // Run before the first await to unlock audio during the user's click.
    await player.activateElement();
    await post('/spotify/transfer', {device_id:deviceId, play:false});
  }
  window.papiBrowserPlayer = {addDeviceOption, preparePlayback};

  async function enablePlayer() {
    if (player) return unlockAudio();
    enable.disabled = true; reconnect.hidden = true;
    message.textContent = 'Connecting browser player…';
    try {
      const status = await api('/spotify/status');
      if (!status.browser_playback_ready) {
        fail('Reconnect Spotify to approve browser streaming access. Spotify Premium is required.', true); return;
      }
      await loadSDK();
      const current = new Spotify.Player({
        name:'PAPI · This browser', volume:0.5,
        getOAuthToken: callback => {
          api('/spotify/browser-token', {method:'POST', headers:{'X-PAPI-Player':'1'}})
            .then(data => { if (player === current) callback(data.access_token); })
            .catch(() => { if (player === current) fail('Could not authorize browser playback. Reconnect Spotify or try again.', true); });
        }
      });
      player = current;
      current.addListener('ready', ({device_id}) => {
        if (player !== current) return;
        clearTimeout(readyTimer); deviceId = device_id;
        const select = document.querySelector('#device-select');
        addDeviceOption(select); select.value = deviceId;
        remember(true);
        message.textContent = 'Browser ready for voice commands. Keep this tab open.';
        here.disabled = false; stop.disabled = false;
        autoplayBlocked = false;
        publish();
        clearInterval(heartbeat); heartbeat = setInterval(publish, 2000);

      });
      current.addListener('not_ready', () => { if (player === current) fail('Browser player went offline. Enable it again to reconnect.'); });
      for (const [event, text, auth] of [
        ['initialization_error', 'This browser cannot play protected Spotify audio. Try Firefox or Chrome with protected content enabled.', false],
        ['authentication_error', 'Spotify authorization failed. Reconnect Spotify to update streaming permissions.', true],
        ['account_error', 'Browser playback requires Spotify Premium on the connected account.', false]
      ]) current.addListener(event, () => { if (player === current) fail(text, auth); });
      current.addListener('autoplay_failed', () => { if (player === current) { autoplayBlocked=true; publish(); message.textContent = 'Audio was blocked. Click Play here once, or allow Autoplay → Allow Audio and Video for this PAPI site in Firefox to use voice without clicks.'; here.focus?.(); } });
      current.addListener('playback_error', () => { if (player === current) message.textContent = 'Spotify could not play this item. Try a different playlist.'; });
      current.addListener('player_state_changed', data => {
        if (!data || player !== current) return;
        if (!data.paused && autoplayBlocked) {
          autoplayBlocked=false; publish();
          message.textContent='Playing in this browser.';
        }
        const track = data.track_window?.current_track;
        state.current = {is_playing:!data.paused, progress_ms:data.position,
          item:track ? {name:track.name, artists:(track.artists || []).map(x => x.name), duration_ms:track.duration_ms} : null};
        renderPlayback();
      });
      readyTimer = setTimeout(() => { if (player === current) fail('Spotify did not make this browser ready. Check Premium and protected-content support, then try again.'); }, 30000);
      if (!await current.connect() && player === current) fail('Could not connect the browser player. Try again.');
    } catch (error) { fail(error.message); }
  }
  enable.addEventListener('click', enablePlayer);
  here.addEventListener('click', () => busy(here, async () => {
    if (!player || !deviceId) throw new Error('Enable the browser player first.');
    await player.activateElement();
    autoplayBlocked=false; publish();
    document.querySelector('#device-select').value = deviceId;
    await post('/spotify/transfer', {device_id:deviceId, play:true});
    message.textContent = 'Playback requested in this browser. If nothing plays, choose a playlist below.';
    await loadMusic();
  }));
  stop.addEventListener('click', () => { remember(false); cleanup(); message.textContent = 'Browser player disconnected. You can still control other Spotify devices.'; });
  window.addEventListener('pagehide', cleanup);
  api('/spotify/status').then(status => {
    reconnect.hidden = status.browser_playback_ready;
    if ((voiceStart || remembered()) && status.browser_playback_ready) { enablePlayer(); return; }
    message.textContent = status.browser_playback_ready ? 'Enable the player to listen here. Keep this tab open.' : 'Reconnect Spotify once to enable browser streaming. Premium required.';
  }).catch(() => { message.textContent = 'Start PAPI and connect Spotify to enable browser playback.'; });
})();

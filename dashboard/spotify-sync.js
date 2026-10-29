/* Follow changes made in Spotify without reloading the workspace. */
(() => {
  let timer, running=false, failures=0;
  function schedule(delay=3000) {
    clearTimeout(timer);
    if(!document.hidden)timer=setTimeout(sync,delay);
  }
  async function sync() {
    clearTimeout(timer);
    if(document.hidden||running)return;
    running=true;
    try {
      // Full connection checks and commands already have their own refresh.
      if(state.spotify.playback_ready&&!state.musicLoading&&!state.musicCommandLoading&&!state.musicSettling) {
        await readPlayback();
        failures=0;
      }
    } catch {
      // Keep the last known song visible during temporary network failures.
      failures=Math.min(failures+1,4);
    } finally {
      running=false;
      schedule(Math.min(3000*2**failures,30000));
    }
  }
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden)clearTimeout(timer);else sync();
  });
  window.addEventListener('focus',sync);
  window.addEventListener('online',()=>{failures=0;return sync();});
  schedule();
})();

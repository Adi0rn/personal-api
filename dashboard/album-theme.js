'use strict';
(()=>{
  const mix=(a,b,t)=>a.map((v,i)=>Math.round(v*(1-t)+b[i]*t));
  const luminance=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
  const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
  const hex=rgb=>'#'+rgb.map(v=>v.toString(16).padStart(2,'0')).join('');
  function paletteFromPixels(pixels){
    const bins=new Map();
    for(let i=0;i+3<pixels.length;i+=4){
      if(pixels[i+3]<200)continue;
      const rgb=[pixels[i],pixels[i+1],pixels[i+2]],low=Math.min(...rgb),high=Math.max(...rgb);
      if(high<28||low>235)continue;
      const delta=high-low,saturation=delta/Math.max(1,high);
      let hue=0;
      if(delta){hue=(high===rgb[0]?(rgb[1]-rgb[2])/delta:high===rgb[1]?2+(rgb[2]-rgb[0])/delta:4+(rgb[0]-rgb[1])/delta)*60;hue=(hue+360)%360;}
      // Group similar hues so a colourful subject is not split into many
      // tiny buckets and lost against a flat grey album background.
      const key=saturation>.22&&delta>24?'h'+(Math.round(hue/20)%18):'neutral';
      const bin=bins.get(key)||{sum:[0,0,0],count:0};
      bin.count++;rgb.forEach((v,c)=>bin.sum[c]+=v);bins.set(key,bin);
    }
    let chosen=null,best=-1;
    for(const bin of bins.values()){
      const rgb=bin.sum.map(v=>Math.round(v/bin.count));
      const saturation=(Math.max(...rgb)-Math.min(...rgb))/Math.max(1,Math.max(...rgb));
      const score=bin.count*(.2+saturation*3);
      if(score>best){chosen=rgb;best=score;}
    }
    // Mostly black/white artwork gets a neutral album treatment.
    chosen=chosen||[145,151,158];
    const bg=mix([8,12,16],chosen,.18),raised=mix([8,12,16],chosen,.28);
    let accent=mix(chosen,[231,237,242],.35);
    const ink=[16,22,28];
    for(let i=0;i<20&&(contrast(accent,bg)<4.5||contrast(accent,raised)<4.5||contrast(accent,ink)<4.5);i++)accent=mix(accent,[255,255,255],.12);
    const muted=mix(chosen,[231,237,242],.8);
    return {bg:hex(bg),raised:hex(raised),accent:hex(accent),hover:hex(mix(accent,[255,255,255],.25)),
      muted:hex(muted),line:hex(mix(bg,accent,.45)),text:'#e7edf2',ink:hex(ink)};
  }
  // Pure palette tests run without a browser, network or personal artwork.
  if(typeof module==='object'&&module.exports){module.exports={paletteFromPixels,contrast};return;}
  const cache=new Map();let activeUrl=null,generation=0,pending=null;
  const panel=()=>document.querySelector('.music-panel');
  function clear(){const el=panel();if(el)el.classList.remove('album-themed');document.body.classList.remove('album-page-themed');}
  function apply(palette){
    const el=panel();if(!el)return;
    for(const [name,value] of Object.entries(palette)){el.style.setProperty('--album-'+name,value);document.body.style.setProperty('--album-'+name,value);}
    el.classList.add('album-themed');document.body.classList.add('album-page-themed');
  }
  function cancelPending(){activeUrl=null;generation++;if(pending){pending.onload=null;pending.onerror=null;pending=null;}}
  function reset(){cancelPending();clear();}
  function update(url){
    if(typeof url!=='string'||!/^https:\/\/(?:i\.scdn\.co|mosaic\.scdn\.co|image-cdn-(?:ak|fa)\.spotifycdn\.com)\//.test(url)){reset();return;}
    if(url===activeUrl)return;
    cancelPending();activeUrl=url;const revision=generation;
    if(cache.has(url)){const palette=cache.get(url);if(palette)apply(palette);else clear();return;}
    const image=new Image();pending=image;image.crossOrigin='anonymous';image.referrerPolicy='no-referrer';
    function remember(palette){cache.set(url,palette);if(cache.size>24)cache.delete(cache.keys().next().value);}
    image.onload=()=>{
      if(revision!==generation||url!==activeUrl)return;
      pending=null;
      try{
        const canvas=document.createElement('canvas');canvas.width=40;canvas.height=40;
        const context=canvas.getContext('2d',{willReadFrequently:true});
        if(!context)throw new Error('Canvas unavailable');
        context.drawImage(image,0,0,40,40);
        const palette=paletteFromPixels(context.getImageData(0,0,40,40).data);
        remember(palette);apply(palette);
      }catch{remember(null);clear();}
    };
    image.onerror=()=>{if(revision===generation&&url===activeUrl){pending=null;remember(null);clear();}};
    image.src=url;
  }
  globalThis.papiAlbumTheme={update,reset};
})();

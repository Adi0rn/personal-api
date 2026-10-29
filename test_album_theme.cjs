const assert=require('node:assert/strict');
const {paletteFromPixels,contrast}=require('./dashboard/album-theme.js');
const rgb=hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
const sample=color=>new Uint8ClampedArray(Array.from({length:1600},()=>[...color,255]).flat());
for(const color of [[220,35,30],[15,45,200],[20,160,60],[235,205,30],[2,2,2],[250,250,250],[120,120,120]]){
  const palette=paletteFromPixels(sample(color));
  assert(contrast(rgb(palette.text),rgb(palette.bg))>=4.5,'Album text must remain legible');
  assert(contrast(rgb(palette.muted),rgb(palette.raised))>=4.5,'Secondary text must remain legible');
  assert(contrast(rgb(palette.accent),rgb(palette.raised))>=4.5,'Controls must remain legible');
  assert(contrast(rgb(palette.accent),rgb(palette.ink))>=4.5,'Play icon must remain legible');
  assert(Math.max(...rgb(palette.bg))<64,'Album backgrounds stay dark');
}
const red=paletteFromPixels(sample([220,35,30])),blue=paletteFromPixels(sample([15,45,200]));
assert.notEqual(red.bg,blue.bg);assert.notEqual(red.accent,blue.accent);
const transparent=new Uint8ClampedArray([255,0,0,0]);
assert.deepEqual(paletteFromPixels(transparent),paletteFromPixels(new Uint8ClampedArray([])));
console.log('Album palettes passed: cover-dependent colours, dark surfaces, readable text/controls, neutral fallback.');

const mixed=new Uint8ClampedArray([...Array.from({length:1100},()=>[100,100,100,255]),...Array.from({length:500},()=>[210,30,25,255])].flat());
const subject=paletteFromPixels(mixed);assert(rgb(subject.accent)[0]>rgb(subject.accent)[1],'Prominent album colours should survive a neutral background');

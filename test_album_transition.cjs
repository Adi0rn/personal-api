const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
function element(){const classes=new Set(),styles=new Map();return {classes,styles,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)},style:{setProperty:(k,v)=>styles.set(k,v)}};}
const panel=element(),body=element(),images=[];let pixels=[210,35,45,255];
const context={document:{body,querySelector:()=>panel,createElement:()=>({getContext:()=>({drawImage(){},getImageData:()=>({data:pixels})})})},Image:class {constructor(){images.push(this);}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'dashboard/album-theme.js'),'utf8'),context);
const theme=context.papiAlbumTheme,url=n=>'https://i.scdn.co/image/'+n;
theme.update(url('first'));images.at(-1).onload();
assert(panel.classes.has('album-themed'));const first=panel.styles.get('--album-bg');
theme.update(url('second'));const stale=images.at(-1).onload;
assert(panel.classes.has('album-themed'),'Previous palette stays active while new artwork loads');
assert.equal(panel.styles.get('--album-bg'),first);
theme.update(url('third'));pixels=[35,70,210,255];stale();assert.equal(panel.styles.get('--album-bg'),first,'Stale artwork cannot recolour the new track');
images.at(-1).onload();assert.notEqual(panel.styles.get('--album-bg'),first);
theme.reset();assert(!panel.classes.has('album-themed'));assert(!body.classes.has('album-page-themed'));
theme.update(url('first'));assert(panel.classes.has('album-themed'),'Cached albums reapply immediately');
theme.update(url('broken'));images.at(-1).onerror();assert(!panel.classes.has('album-themed'),'Failed artwork restores the normal theme');
console.log('Album transitions passed: previous palette retained, stale artwork ignored, reset, cached colours, and failure fallback.');

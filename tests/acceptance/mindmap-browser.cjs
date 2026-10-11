// Usage: NODE_PATH=<playwright packages> node tests/acceptance/mindmap-browser.cjs <mindmap.html> <output dir>
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.MINDMAP_BROWSER_CHANNEL||undefined});
 const context=await browser.newContext({acceptDownloads:true,offline:true});
 const page=await context.newPage(),requests=[],errors=[];
 page.on('request',r=>{if(!/^(file|data|blob):/.test(r.url()))requests.push(r.url());});
 page.on('pageerror',e=>errors.push(String(e)));
 await context.route(/https?:\/\//,route=>route.abort());
 const input=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);fs.mkdirSync(out,{recursive:true});
 await page.goto('file://'+input);
 await page.locator('.node').first().click();
 await page.locator('#expand').click();
 await page.locator('.node').first().focus();await page.keyboard.press('Enter');await page.locator('#expand').click();
 await page.locator('#map').hover();await page.mouse.wheel(0,-100);
 await page.locator('#fit').click();
 for(const type of ['svg','png']){
  const promise=page.waitForEvent('download');await page.locator('#'+type+'-export').click();
  const d=await promise;assert.equal(await d.failure(),null);const dest=path.join(out,d.suggestedFilename());await d.saveAs(dest);
  const bytes=fs.readFileSync(dest);assert(bytes.length>100);
  if(type==='png')assert.equal(bytes.subarray(1,4).toString(),'PNG');else assert(bytes.toString().includes('<svg'));
 }
 await page.setViewportSize({width:375,height:812});await page.locator('#fit').click();await page.screenshot({path:path.join(out,'mobile.png')});
 await page.setViewportSize({width:1440,height:1000});await page.locator('#fit').click();await page.screenshot({path:path.join(out,'desktop.png')});
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({offline:true,attemptedNetworkRequests:requests,consoleErrors:errors,exports:['SVG','PNG'],nodes:await page.locator('.node').count()}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});

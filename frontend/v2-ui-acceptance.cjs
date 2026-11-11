const {chromium}=require('@playwright/test');
const path=require('path');const fs=require('fs');
(async()=>{
 const dir=path.resolve('../.runtime-v02/acceptance/screenshots');fs.mkdirSync(dir,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8842');
 await page.getByLabel('账号',{exact:true}).fill('acceptance');await page.getByLabel('密码',{exact:true}).fill('acceptance-only-2026');await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('heading',{name:'生产运行',exact:true}).waitFor();
 await page.screenshot({path:path.join(dir,'production.png'),fullPage:true});
 for(const [title,name] of [['产品与工艺','products'],['数据与语义','semantics'],['异常与方案','scenarios'],['证据与回放','evidence'],['系统管理','administration']]){
   await page.locator('nav').getByRole('button',{name:title,exact:true}).click();await page.getByRole('heading',{name:title,exact:true}).waitFor();
   if(name==='products'){
     await page.getByRole('button',{name:/OP 03/}).click();await page.getByText('精铣 · 设备要求与资格匹配').waitFor();
     await page.getByRole('button',{name:'查看要求依据 ↗'}).click();await page.locator('.drawer').getByText('P1/MILL',{exact:true}).first().waitFor();
     await page.screenshot({path:path.join(dir,'qualification-evidence.png'),fullPage:true});await page.locator('.drawer-title button').click();
   }
   if(name==='semantics')await page.locator('.network canvas').waitFor();
   await page.screenshot({path:path.join(dir,name+'.png'),fullPage:true});
 }
 console.log(JSON.stringify({passed:errors.length===0,errors,screenshots:dir}));await browser.close();if(errors.length)process.exitCode=1;
})();
